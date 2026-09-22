"""FlowerSlots service for managing display arrangements in Dorf 2."""

from loguru import logger

from app.core.client import MFFGameClient
from app.modules.farmersmarket.models import FlowerSlotItem, FlowerSlotsState
from app.services.stock_service import StockService


class FlowerSlotsService:
    """Manages the flower arrangement showcase slots in Dorf 2 to earn points."""

    def __init__(self, client: MFFGameClient, stock_service: StockService | None = None) -> None:
        self.client = client
        self.stock_service = stock_service
        self.state = FlowerSlotsState()

    def update(self, farmersmarket_data: dict) -> None:
        """Parse display slots from farmersmarket data block."""
        fs_raw = farmersmarket_data.get("flower_slots", {})
        if not isinstance(fs_raw, dict):
            return

        self.state.total_points = int(fs_raw.get("points", 0) or 0)
        parsed_slots: dict[int, FlowerSlotItem] = {}

        slots_dict = fs_raw.get("slots", {})
        if isinstance(slots_dict, dict):
            for s_str, s_data in slots_dict.items():
                try:
                    s_id = int(s_str)
                    pid_val = s_data.get("pid")
                    pid = int(pid_val) if pid_val is not None and str(pid_val).isdigit() else None
                    item = FlowerSlotItem(
                        slot_id=s_id,
                        pid=pid,
                        points=int(s_data.get("points", 0) or 0),
                        remain=int(s_data.get("remain", 0) or 0),
                        waterremain=int(s_data.get("waterremain", 0) or 0),
                    )
                    parsed_slots[s_id] = item
                except (ValueError, TypeError) as e:
                    logger.debug(f"FlowerSlotsService: Konnte Schau-Slot {s_str} nicht parsen: {e}")

        # Ensure slot 1 is at least tracked if empty
        if 1 not in parsed_slots:
            parsed_slots[1] = FlowerSlotItem(slot_id=1, pid=None)

        self.state.slots = parsed_slots

    async def remove_expired(self) -> int:
        """Remove expired arrangements from showcase slots."""
        removed_count = 0
        for slot in self.state.slots.values():
            if slot.is_expired:
                logger.info(f"Schau-Slots: Entferne abgelaufenes Gesteck aus Slot #{slot.slot_id}...")
                try:
                    res = await self.client.api_call(
                        "farm",
                        {
                            "mode": "flowerslot_remove",
                            "farm": 1,
                            "position": 1,
                            "set": f"{slot.slot_id}:1",
                        },
                    )
                    removed_count += 1
                    slot.pid = None
                    slot.remain = 0
                    if "updateblock" in res and self.stock_service:
                        self.stock_service.update(res)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Schau-Slots: Fehler beim Entfernen aus Slot #{slot.slot_id}: {e}")

        return removed_count

    async def water(self) -> int:
        """Water showcase arrangements that need water."""
        watered_count = 0
        for slot in self.state.slots.values():
            if slot.needs_water:
                logger.info(f"Schau-Slots: Gieße Schau-Gesteck in Slot #{slot.slot_id}...")
                try:
                    res = await self.client.api_call(
                        "farm",
                        {
                            "mode": "flowerslot_water",
                            "farm": 1,
                            "position": 1,
                            "set": f"{slot.slot_id}:1",
                        },
                    )
                    watered_count += 1
                    slot.waterremain = 86400
                    if "updateblock" in res and self.stock_service:
                        self.stock_service.update(res)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Schau-Slots: Fehler beim Gießen von Slot #{slot.slot_id}: {e}")

        return watered_count

    async def plant_arrangement(self) -> bool:
        """Place an available flower arrangement from inventory into an empty display slot."""
        slot_1 = self.state.slots.get(1)
        if not slot_1 or not slot_1.is_empty:
            return False

        if not self.stock_service:
            return False

        # Find best arrangement in stock (category 'fla')
        best_pid: int | None = None
        for pid, product in self.stock_service.products.items():
            if getattr(product, "category", "") == "fla" and product.amount > 0:
                best_pid = pid
                break

        if not best_pid:
            return False

        logger.info(f"Schau-Slots: Stelle Gesteck PID {best_pid} in Schau-Slot #1 aus...")
        try:
            res = await self.client.api_call(
                "farm",
                {
                    "mode": "flowerslot_plant",
                    "farm": 1,
                    "position": 1,
                    "set": f"1:{best_pid}",
                },
            )
            slot_1.pid = best_pid
            slot_1.remain = 86400  # Default display time
            if "updateblock" in res and self.stock_service:
                self.stock_service.update(res)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Schau-Slots: Fehler beim Ausstellen von PID {best_pid}: {e}")
            return False
