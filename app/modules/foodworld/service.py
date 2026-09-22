"""FoodworldService: Master orchestrator for kitchen cooking, table management, and market export."""

from loguru import logger

from app.config import FoodworldConfig
from app.core.client import MFFGameClient
from app.modules.foodworld.kitchen import KitchenService
from app.modules.foodworld.models import FoodworldSummary
from app.modules.foodworld.tables import TableService
from app.services.market_service import MarketService
from app.services.stock_service import StockService
from app.services.trade_service import TradeService


class FoodworldService:
    """Coordinates Kitchens, Tables, Guest Seating, and Market Export in Foodworld."""

    def __init__(
        self,
        client: MFFGameClient,
        stock_service: StockService | None = None,
        market_service: MarketService | None = None,
        trade_service: TradeService | None = None,
        config: FoodworldConfig | None = None,
    ) -> None:
        self.client = client
        self.stock_service = stock_service
        self.market_service = market_service or (stock_service.market if stock_service else None)
        self.trade_service = trade_service
        self.config = config or FoodworldConfig()

        self.kitchen = KitchenService(client, stock_service)
        self.tables = TableService(client, stock_service)

        self.total_revenue_kt: float = 0.0
        self.total_dishes_exported: int = 0

    async def run_cycle(self) -> dict:
        """Execute a full Foodworld automation cycle."""
        if not self.config.enabled:
            logger.debug("FoodworldService: Modul ist laut Konfiguration deaktiviert.")
            return {"enabled": False}

        logger.info("=== Starte Foodworld-Zyklus (Picknick-Bereich) ===")
        results: dict = {
            "picked_dishes": 0,
            "cooked_dishes": 0,
            "cashed_guests": 0,
            "revenue_kt": 0.0,
            "seated_guests": 0,
            "exported_dishes": 0,
        }

        # 1. Fetch current status from foodworld.php
        try:
            res = await self.client.api_call(
                "foodworld",
                {"action": "foodworld_init", "id": 0, "table": 0, "chair": 0},
            )
            db = res.get("datablock", {})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"FoodworldService: Fehler beim Abrufen der Foodworld-Daten: {e}")
            return results

        if not db or not isinstance(db, dict):
            logger.warning("FoodworldService: Keine gültigen Daten in Upstream-Antwort.")
            return results

        # 2. Update sub-services
        self.kitchen.update(db)
        self.tables.update(db)

        # 3. Cash out completed meals (revenue credited in kT directly)
        if self.config.auto_cash_tables:
            try:
                cashed, rev = await self.tables.cash_tables()
                results["cashed_guests"] = cashed
                results["revenue_kt"] = rev
                self.total_revenue_kt += rev
            except Exception as e:  # noqa: BLE001
                logger.warning(f"FoodworldService: Fehler beim Abkassieren: {e}")

        # 4. Pick up finished dishes from kitchen slots
        if self.config.auto_cook:
            try:
                picked = await self.kitchen.pickup_products()
                results["picked_dishes"] = picked
            except Exception as e:  # noqa: BLE001
                logger.warning(f"FoodworldService: Fehler beim Abholen fertiger Speisen: {e}")

        # 5. Cook new dishes (prioritizing waiting farmi orders and dishes below buffer)
        if self.config.auto_cook:
            try:
                demanded_cart = self.tables.get_demanded_cart()
                cooked = await self.kitchen.produce(
                    demanded_cart=demanded_cart,
                    reserve_buffer=self.config.dish_reserve_buffer,
                    auto_buy_ingredients=self.config.auto_buy_ingredients,
                )
                results["cooked_dishes"] = cooked
            except Exception as e:  # noqa: BLE001
                logger.warning(f"FoodworldService: Fehler beim Kochen: {e}")

        # 6. Seat waiting guests at free chairs
        if self.config.auto_seat_guests:
            try:
                seated = await self.tables.seat_guests()
                results["seated_guests"] = seated
            except Exception as e:  # noqa: BLE001
                logger.warning(f"FoodworldService: Fehler beim Platzieren von Gästen: {e}")

        # 7. Market export: Sell surplus dishes strictly when no market offer exists
        if self.config.market_export_enabled and self.stock_service:
            try:
                exported = await self._export_surplus_dishes()
                results["exported_dishes"] = exported
                self.total_dishes_exported += exported
            except Exception as e:  # noqa: BLE001
                logger.warning(f"FoodworldService: Fehler beim Marktexport: {e}")

        logger.info(
            f"=== Foodworld-Zyklus abgeschlossen: "
            f"Speisen geerntet/gekocht: {results['picked_dishes']}/{results['cooked_dishes']}, "
            f"Gäste abkassiert/platziert: {results['cashed_guests']}/{results['seated_guests']}, "
            f"Einnahmen: +{results['revenue_kt']:.2f} kT, Marktexport: {results['exported_dishes']} Stk. ==="
        )
        return results

    async def _export_surplus_dishes(self, max_items_per_batch: int = 100) -> int:
        """Export foodworld dishes with stock > reserve_buffer to market ONLY if no offers exist."""
        if not self.stock_service or not self.market_service:
            return 0

        exported_total = 0
        reserve = self.config.dish_reserve_buffer

        # Scan for products in category 'fw' or PIDs in 130..169 and 450..485
        for pid, product in self.stock_service.products.items():
            cat = getattr(product, "category", "")
            is_fw = cat == "fw" or (130 <= pid <= 169) or (450 <= pid <= 485)
            if not is_fw:
                continue

            current_stock = self.stock_service.get_amount(pid)
            if current_stock <= reserve:
                continue

            surplus = current_stock - reserve
            # Minimum batch size of 5 to avoid tiny micro-offers
            if surplus < 5:
                continue

            # Check rule: "Biete die Produkte nur dann am Markt an, wenn bisher kein Angebot dafür vorhanden ist"
            if self.config.only_empty_market:
                try:
                    offers = await self.market_service.get_offers(pid)
                    if offers:
                        logger.debug(
                            f"Foodworld-Marktexport: Überspringe PID {pid} ('{product.name}'), "
                            f"da bereits {len(offers)} Angebot(e) am Markt existieren."
                        )
                        continue
                except Exception as e:  # noqa: BLE001
                    logger.debug(
                        f"Foodworld-Marktexport: Konnte Marktangebote für PID {pid} nicht prüfen: {e}"
                    )
                    continue

            # Create market offer for surplus (capped at max_items_per_batch)
            sell_amount = min(surplus, max_items_per_batch)
            # Price determination: base catalog price, optionally with 10% premium for empty market
            base_price = product.price if product.price > 0 else 100.0
            sell_price = round(base_price * 1.0, 2)

            logger.info(
                f"Foodworld-Marktexport: Markt ist leer für PID {pid} ('{product.name}')! "
                f"Stelle {sell_amount}x für je {sell_price:.2f} kT zum Verkauf ein (Reserve: {reserve} Stk.)..."
            )
            try:
                res = await self.client.api_call(
                    "city",
                    {
                        "mode": "marketcreateoffer",
                        "pid": pid,
                        "amount": sell_amount,
                        "price": sell_price,
                        "comp": 1,
                    },
                )
                if res.get("datablock") == 1 or (
                    isinstance(res.get("datablock"), list) and res.get("datablock") and res.get("datablock")[0] == 1
                ):
                    exported_total += sell_amount
                    # Reactively update stock
                    if "updateblock" in res and self.stock_service:
                        self.stock_service.update(res)
                    else:
                        # Deduct manually from in-memory amount
                        product.amount -= sell_amount
                else:
                    logger.warning(f"Foodworld-Marktexport: Marktangebot abgewiesen: {res}")
                    block_msg = str(res).lower()
                    if "20 angebote" in block_msg or "nicht möglich" in block_msg or "voll" in block_msg:
                        logger.warning(
                            "Foodworld-Marktexport: Maximales Marktlimit erreicht (20 Angebote). "
                            "Breche weitere Exporte für diesen Zyklus ab."
                        )
                        break
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Foodworld-Marktexport: Fehler beim Einstellen von PID {pid}: {e}")
                break

        return exported_total

    def get_summary(self) -> FoodworldSummary:
        """Return structured summary for REST API and Web Dashboard."""
        total_slots = sum(len(b.slots) for b in self.kitchen.buildings.values())
        active_slots = sum(
            sum(1 for s in b.slots.values() if s.pid is not None and not s.is_free)
            for b in self.kitchen.buildings.values()
        )
        ready_slots = sum(len(b.ready_slots) for b in self.kitchen.buildings.values())

        unlocked_tables = sum(1 for t in self.tables.tables.values() if t.is_unlocked)
        total_chairs = sum(len(t.chairs) for t in self.tables.tables.values() if t.is_unlocked)
        occupied_chairs = sum(
            sum(1 for c in t.chairs.values() if not c.is_free)
            for t in self.tables.tables.values()
            if t.is_unlocked
        )
        ready_chairs = sum(
            len(t.ready_chairs) for t in self.tables.tables.values() if t.is_unlocked
        )

        waiting_farmis = sum(1 for f in self.tables.farmis if f.status == 0)

        details = {
            "buildings": [
                {
                    "id": b.id,
                    "name": b.name,
                    "level": b.level,
                    "slots": [
                        {
                            "slot_id": s.slot_id,
                            "pid": s.pid,
                            "remain": s.remain,
                            "ready": s.ready,
                            "is_blocked": s.is_blocked,
                        }
                        for s in b.slots.values()
                    ],
                }
                for b in self.kitchen.buildings.values()
            ],
            "tables": [
                {
                    "table_id": t.table_id,
                    "is_unlocked": t.is_unlocked,
                    "chairs": [
                        {
                            "chair_id": c.chair_id,
                            "farmi_id": c.farmi_id,
                            "remain": c.remain,
                            "ready": c.ready,
                        }
                        for c in t.chairs.values()
                    ],
                }
                for t in self.tables.tables.values()
            ],
            "waiting_farmis": [
                {
                    "id": f.id,
                    "price": f.price,
                    "cart": f.cart,
                }
                for f in self.tables.farmis
                if f.status == 0
            ],
        }

        return FoodworldSummary(
            enabled=self.config.enabled,
            kitchen_slots_total=total_slots,
            kitchen_slots_active=active_slots,
            kitchen_slots_ready=ready_slots,
            tables_unlocked=unlocked_tables,
            chairs_total=total_chairs,
            chairs_occupied=occupied_chairs,
            chairs_ready=ready_chairs,
            farmis_waiting=waiting_farmis,
            dishes_exported_count=self.total_dishes_exported,
            revenue_collected_kt=self.total_revenue_kt,
            details=details,
        )
