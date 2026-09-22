"""Gärtnerei (Nursery) service for crafting flower arrangements in Dorf 2."""

from loguru import logger

from app.core.client import MFFGameClient
from app.modules.farmersmarket.models import NurseryProduct, NurserySlot, NurseryState
from app.modules.farmersmarket.order_manager import FlowerOrderManager
from app.services.stock_service import StockService


class NurseryService:
    """Automates harvesting and crafting of flower arrangements in the nursery."""

    def __init__(self, client: MFFGameClient, stock_service: StockService | None = None) -> None:
        self.client = client
        self.stock_service = stock_service
        self.state = NurseryState()

    def update(self, farmersmarket_data: dict) -> None:
        """Parse nursery slots and recipe catalog from the upstream farmersmarket data block."""
        nursery_raw = farmersmarket_data.get("nursery", {})
        if not nursery_raw:
            return

        # 1. Parse slots
        parsed_slots: dict[int, NurserySlot] = {}
        for slot_str, s_data in nursery_raw.get("slots", {}).items():
            try:
                s_id = int(slot_str)
                pid = int(s_data["pid"]) if "pid" in s_data and s_data["pid"] is not None else None
                slot = NurserySlot(
                    slot_id=s_id,
                    pid=pid,
                    duration=int(s_data.get("duration", 0) or 0),
                    createdate=int(s_data.get("createdate", 0) or 0),
                    remain=int(s_data.get("remain", 0) or 0),
                    block=int(s_data.get("block", 0) or 0),
                    cost=int(s_data.get("cost", 0) or 0),
                    coins=int(s_data.get("coins", 0) or 0),
                )
                parsed_slots[s_id] = slot
            except (ValueError, TypeError) as e:
                logger.debug(f"NurseryService: Konnte Slot {slot_str} nicht parsen: {e}")

        # Slot 1 is always unlocked; if omitted by upstream when idle, initialize as free
        if 1 not in parsed_slots:
            parsed_slots[1] = NurserySlot(slot_id=1, pid=None, remain=0, block=0, coins=0)

        self.state.slots = parsed_slots

        # 2. Parse products / recipes
        parsed_products: dict[int, NurseryProduct] = {}
        for prod_str, p_data in nursery_raw.get("products", {}).items():
            try:
                p_id = int(prod_str)
                # Requirements map: ingredient_pid -> amount
                reqs_raw = p_data.get("products", {})
                reqs: dict[int, int] = {}
                if isinstance(reqs_raw, dict):
                    for ing_str, amt in reqs_raw.items():
                        reqs[int(ing_str)] = int(amt)

                prod_name = None
                if self.stock_service and p_id in self.stock_service.products:
                    prod_name = self.stock_service.products[p_id].name
                name = prod_name or p_data.get("name", f"Gesteck #{p_id}")

                product = NurseryProduct(
                    id=p_id,
                    pid=p_id,
                    name=name,
                    farmipoints=int(p_data.get("farmipoints", 0) or 0),
                    duration=int(p_data.get("duration", 0) or 0),
                    crop=int(p_data.get("crop", 0) or 0),
                    cost=int(p_data.get("cost", 0) or 0),
                    coins=int(p_data.get("coins", 0) or 0),
                    requirements=reqs,
                )
                parsed_products[p_id] = product
            except (ValueError, TypeError) as e:
                logger.debug(f"NurseryService: Konnte Produkt {prod_str} nicht parsen: {e}")

        self.state.products = parsed_products
        logger.debug(
            f"NurseryService: Aktualisiert mit {len(self.state.slots)} Slots "
            f"und {len(self.state.products)} Rezepten."
        )

    async def harvest(self) -> int:
        """Harvest all finished arrangements in ready slots."""
        harvested_count = 0
        for slot in self.state.slots.values():
            if slot.is_ready:
                logger.info(f"Gärtnerei: Ernte fertiges Gesteck aus Slot #{slot.slot_id} (PID {slot.pid})...")
                try:
                    res = await self.client.api_call(
                        "farm",
                        {
                            "mode": "nursery_harvest",
                            "farm": 1,
                            "position": 1,
                            "id": slot.slot_id,
                            "slot": slot.slot_id,
                        },
                    )
                    harvested_count += 1
                    # Mark slot locally as free
                    slot.pid = None
                    slot.remain = 0
                    if "updateblock" in res and self.stock_service:
                        self.stock_service.update(res)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Gärtnerei: Fehler beim Ernten von Slot #{slot.slot_id}: {e}")

        return harvested_count

    async def produce(self, order_manager: FlowerOrderManager) -> int:
        """Craft flower arrangements in available, unblocked slots strictly according to demand."""
        if not order_manager.has_orders:
            logger.debug("Gärtnerei: Keine Aufträge im OrderManager vorhanden - keine Neuproduktion.")
            return 0

        started_count = 0
        # Find free slots that don't cost coins
        free_slots = [s for s in self.state.slots.values() if s.is_free and not s.is_blocked]
        if not free_slots:
            return 0

        # Check arrangements already in production to avoid overproducing
        active_pids = {s.pid for s in self.state.slots.values() if not s.is_free and s.pid}

        demanded_pids = order_manager.get_orders()
        for slot in free_slots:
            # Pick next demanded arrangement that can be crafted
            recipe_to_start: NurseryProduct | None = None
            for pid in demanded_pids:
                if pid in active_pids:
                    continue
                recipe = self.state.products.get(pid)
                if not recipe or recipe.coins > 0:
                    continue

                if self._has_sufficient_ingredients(recipe):
                    recipe_to_start = recipe
                    break

            if not recipe_to_start:
                break

            logger.info(
                f"Gärtnerei: Starte Produktion von Gesteck PID {recipe_to_start.pid} "
                f"in Slot #{slot.slot_id}..."
            )
            try:
                res = await self.client.api_call(
                    "farm",
                    {
                        "mode": "nursery_startproduction",
                        "farm": 1,
                        "position": 1,
                        "id": recipe_to_start.pid,
                        "pid": recipe_to_start.pid,
                        "slot": slot.slot_id,
                    },
                )
                started_count += 1
                slot.pid = recipe_to_start.pid
                slot.remain = recipe_to_start.duration
                order_manager.consume_order(recipe_to_start.pid, 1)

                if "updateblock" in res and self.stock_service:
                    self.stock_service.update(res)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Gärtnerei: Fehler beim Starten von PID {recipe_to_start.pid}: {e}")

        return started_count

    def _has_sufficient_ingredients(self, recipe: NurseryProduct) -> bool:
        """Check if all required flower components are in stock."""
        if not self.stock_service:
            return True

        for ing_pid, needed_amt in recipe.requirements.items():
            avail = self.stock_service.get_amount(ing_pid)
            if avail < needed_amt:
                return False
        return True
