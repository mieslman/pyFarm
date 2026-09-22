from datetime import datetime
from typing import Any, Optional
from loguru import logger

from app.config import settings
from app.core.client import MFFGameClient
from app.modules.insecthotel.models import (
    InsectCheckout,
    InsectHotelSnapshot,
    InsectHotelSummary,
    InsectNicheSlot,
    InsectStockSlot,
)


class InsectHotelService:
    """Manages the Insect Hotel: monitoring populations, refilling food stock, and collecting checkout."""

    def __init__(self, client: MFFGameClient):
        self.client = client
        self.snapshot: Optional[InsectHotelSnapshot] = None

    async def init_remote(self, stock_service: Optional[Any] = None) -> Optional[InsectHotelSnapshot]:
        """Fetch current state from game server and construct snapshot."""
        try:
            res = await self.client.api_call("farm", {"mode": "insecthotel_init"})
            hotel_data = res.get("updateblock", {}).get("map", {}).get("insecthotel", {})
            if not hotel_data or "data" not in hotel_data:
                logger.debug("InsectHotelService: Kein Insektenhotel vorhanden oder freigeschaltet.")
                return None

            data = hotel_data.get("data", {})
            config = hotel_data.get("config", {})

            # 1. Parse living niche slots
            slots_map: dict[str, InsectNicheSlot] = {}
            total_pop = 0
            cfg_slots = config.get("slots", {})

            for slot_id, s_info in data.get("slots", {}).items():
                slot_cfg = cfg_slots.get(str(slot_id), {})
                name = slot_cfg.get("name", f"Slot {slot_id}")
                pop = int(s_info.get("population", 0))
                total_pop += pop
                slots_map[str(slot_id)] = InsectNicheSlot(
                    slot_id=str(slot_id),
                    name=name,
                    level=int(s_info.get("level", 1)),
                    population=pop,
                    happiness=float(s_info.get("happiness", 0.0)),
                )

            # 2. Parse feeding stock slots
            stock_slots_map: dict[str, InsectStockSlot] = {}
            stock_levels_cfg = config.get("stock_level", {})

            for stock_id, st_info in data.get("stock", {}).items():
                st_lvl = str(st_info.get("level", 1))
                cap = int(stock_levels_cfg.get(st_lvl, {}).get("capacity", 60))
                raw_pid = st_info.get("pid")
                pid = int(raw_pid) if raw_pid is not None and str(raw_pid).isdigit() else None
                amt = int(st_info.get("amount", 0))

                p_name = f"PID {pid}" if pid else "Leer"
                if pid and stock_service:
                    p_obj = stock_service.get_product(pid)
                    if p_obj:
                        p_name = p_obj.name

                stock_slots_map[str(stock_id)] = InsectStockSlot(
                    slot_id=str(stock_id),
                    level=int(st_lvl),
                    pid=pid,
                    product_name=p_name,
                    amount=amt,
                    capacity=cap,
                )

            # 3. Parse checkout
            chk_data = data.get("checkout", {})
            chk_lvl = str(chk_data.get("level", 1))
            chk_limits = config.get("checkout_level", {}).get(chk_lvl, {}).get("limit", {})
            limit_money = float(chk_limits.get("money", 3000.0))
            limit_points = int(chk_limits.get("points", 60000))

            checkout = InsectCheckout(
                level=int(chk_lvl),
                money=float(chk_data.get("money", 0.0)),
                points=int(chk_data.get("points", 0)),
                limit_money=limit_money,
                limit_points=limit_points,
            )

            snapshot = InsectHotelSnapshot(
                id=str(data.get("id", "")),
                slots=slots_map,
                stock_slots=stock_slots_map,
                checkout=checkout,
                total_population=total_pop,
                last_updated=datetime.now().isoformat(),
            )
            self.snapshot = snapshot
            return snapshot

        except Exception as e:
            logger.warning(f"InsectHotelService: Fehler beim Abruf von insecthotel_init: {e}")
            return None

    async def collect_checkout(self, force: bool = False) -> bool:
        """Empty cash register if money or points exceed the configured threshold, or if force is True."""
        if not self.snapshot:
            return False

        chk = self.snapshot.checkout
        threshold = settings.insecthotel.checkout_threshold_percent

        should_collect = (
            force
            or chk.fill_ratio_money >= threshold
            or chk.fill_ratio_points >= threshold
        )

        if not should_collect:
            logger.debug(
                f"InsectHotelService: Kasse nicht zur Leerung bereit (Geld: {chk.money:.2f}/{chk.limit_money:.2f} kT "
                f"[{chk.fill_ratio_money * 100:.1f}%], Punkte: {chk.points}/{chk.limit_points} [{chk.fill_ratio_points * 100:.1f}%])."
            )
            return False

        logger.info(
            f"InsectHotelService: Leere Hotelkasse: +{chk.money:.2f} kT und +{chk.points} Punkte..."
        )
        try:
            res = await self.client.api_call("farm", {"mode": "insecthotel_collect_checkout"})
            datablock = res.get("datablock")
            if datablock == 1 or (isinstance(datablock, list) and datablock and datablock[0] == 1):
                logger.info("InsectHotelService: Kasse erfolgreich geleert.")
                chk.money = 0.0
                chk.points = 0
                return True
            else:
                logger.warning(f"InsectHotelService: Unerwartete Antwort beim Kassenleeren: {res}")
                return False
        except Exception as e:
            logger.error(f"InsectHotelService: Fehler beim Kassenleeren: {e}")
            return False

    async def refill_stock(self, stock_service: Optional[Any] = None, force: bool = False) -> int:
        """Refill feeding compartments if stock dropped below threshold, or if force is True."""
        if not self.snapshot:
            return 0

        threshold = settings.insecthotel.refill_threshold_percent
        reserve = settings.insecthotel.min_stock_reserve
        refilled_count = 0

        # Procure missing feed plants via marketplace / seed dealer if enabled
        if (
            settings.insecthotel.auto_buy_feed
            and stock_service
            and hasattr(stock_service, "grasp_products")
        ):
            missing_requirements: list[dict[str, int]] = []
            for slot in self.snapshot.stock_slots.values():
                if not slot.pid or slot.missing_amount <= 0:
                    continue
                if not force and slot.missing_amount <= slot.capacity * threshold:
                    continue

                avail = stock_service.get_stock(slot.pid)
                usable = max(0, avail - reserve)
                if usable < slot.missing_amount:
                    missing_requirements.append({"pid": slot.pid, "amount": slot.missing_amount})

            if missing_requirements:
                logger.info(
                    f"InsectHotelService: Beschaffe fehlende Futterpflanzen über Markt/Saatguthändler: {missing_requirements}"
                )
                try:
                    await stock_service.grasp_products(missing_requirements)
                except Exception as e:
                    logger.error(f"InsectHotelService: Fehler bei der Futterbeschaffung (grasp_products): {e}")

        for slot_id, slot in self.snapshot.stock_slots.items():
            if not slot.pid:
                continue

            refill_needed = slot.missing_amount
            # Only refill if missing amount is greater than threshold percentage of capacity (unless forced)
            if not force and refill_needed <= slot.capacity * threshold:
                continue
            if refill_needed <= 0:
                continue

            # Check available inventory in main stock
            available_stock = 999999
            if stock_service:
                available_stock = stock_service.get_stock(slot.pid)

            usable_amount = max(0, available_stock - reserve)
            if usable_amount <= 0:
                logger.debug(
                    f"InsectHotelService: Nicht genügend '{slot.product_name}' im Hauptlager "
                    f"(Bestand: {available_stock}, Reserve: {reserve})."
                )
                continue

            amount_to_refill = min(refill_needed, usable_amount)
            if amount_to_refill <= 0:
                continue

            logger.info(
                f"InsectHotelService: Fülle Lagerslot {slot_id} mit {amount_to_refill}x '{slot.product_name}' "
                f"(PID {slot.pid}) auf (Kapazität: {slot.capacity}, Füllstand vorher: {slot.amount})..."
            )
            try:
                res = await self.client.api_call(
                    "farm",
                    {
                        "mode": "insecthotel_set_stockslot",
                        "slot": slot_id,
                        "pid": slot.pid,
                        "amount": amount_to_refill,
                    },
                )
                datablock = res.get("datablock")
                if datablock == 1 or (isinstance(datablock, list) and datablock and datablock[0] == 1):
                    slot.amount += amount_to_refill
                    refilled_count += 1
                    logger.info(f"InsectHotelService: Lagerslot {slot_id} erfolgreich aufgefüllt.")
                    # Update stock_service internal cache if present
                    if stock_service:
                        if hasattr(stock_service, "deduct_stock"):
                            stock_service.deduct_stock(slot.pid, amount_to_refill, farm_id=1)
                        elif hasattr(stock_service, "main_stock"):
                            stock_service.main_stock[slot.pid] = max(0, available_stock - amount_to_refill)
                else:
                    logger.warning(f"InsectHotelService: Fehler beim Auffüllen von Slot {slot_id}: {res}")
            except Exception as e:
                logger.error(f"InsectHotelService: Fehler beim Auffüllen von Slot {slot_id}: {e}")

        return refilled_count

    async def serve(self, stock_service: Optional[Any] = None) -> dict[str, Any]:
        """Orchestrate regular inspection, checkout collection, and stock refilling."""
        if not settings.insecthotel.enabled:
            logger.debug("InsectHotelService: Modul ist in Konfiguration deaktiviert.")
            return {"active": False, "enabled": False}

        logger.info("========== Insektenhotel: Starte Bewirtschaftungszyklus ==========")
        snapshot = await self.init_remote(stock_service=stock_service)
        if not snapshot:
            logger.info("========== Insektenhotel: Kein aktives Gebäude gefunden ==========")
            return {"active": False}

        collected = False
        if settings.insecthotel.auto_collect_checkout:
            collected = await self.collect_checkout()

        refilled_slots = 0
        if settings.insecthotel.auto_refill_stock:
            refilled_slots = await self.refill_stock(stock_service=stock_service)

        logger.info(
            f"========== Insektenhotel: Zyklus abgeschlossen (Population: {snapshot.total_population}, "
            f"Kasse geleert: {collected}, Slots aufgefüllt: {refilled_slots}) =========="
        )

        return {
            "active": True,
            "hotel_id": snapshot.id,
            "total_population": snapshot.total_population,
            "collected_checkout": collected,
            "refilled_slots": refilled_slots,
            "checkout_money": snapshot.checkout.money,
            "checkout_points": snapshot.checkout.points,
        }

    def get_summary(self) -> InsectHotelSummary:
        """Return compact summary for REST API and dashboard."""
        if not self.snapshot:
            return InsectHotelSummary(active=False)

        chk = self.snapshot.checkout
        return InsectHotelSummary(
            active=True,
            hotel_id=self.snapshot.id,
            total_population=self.snapshot.total_population,
            slots_count=len(self.snapshot.slots),
            stock_slots_count=len(self.snapshot.stock_slots),
            checkout_money=chk.money,
            checkout_points=chk.points,
            checkout_money_limit=chk.limit_money,
            checkout_points_limit=chk.limit_points,
            last_updated=self.snapshot.last_updated,
        )
