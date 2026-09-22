from typing import Any

from loguru import logger

from app.core.client import MFFGameClient
from app.modules.sushibar.models import SushiFarmi, SushiRecipe, SushiTrainSlot
from app.services.stock_service import StockService


class SushiTrainService:
    """Manages the 16-slot sushi conveyor belt (train).

    Note: As per user requirement, conveyor belt automation is inactive by default (auto_train = False).
    """

    def __init__(self, client: MFFGameClient):
        self.client = client
        self.slots: list[SushiTrainSlot] = []

    def update_slots(
        self,
        raw_train: dict[str, Any] | None,
        recipes: dict[int, SushiRecipe],
        catalog: dict[int, Any] | None = None,
    ):
        """Parse raw train slots from sushibar response."""
        self.slots = []
        if not isinstance(raw_train, dict):
            return

        for slot_str, data in raw_train.items():
            if not str(slot_str).isdigit() or not isinstance(data, dict):
                continue
            slot_nr = int(slot_str)
            pos = int(data.get("pos", 0))
            buy_time = data.get("buy_time")
            pid = int(data["pid"]) if data.get("pid") else None

            prod_name = ""
            if pid:
                if catalog and pid in catalog:
                    prod_name = catalog[pid].name
                elif pid in recipes:
                    prod_name = recipes[pid].name

            self.slots.append(
                SushiTrainSlot(
                    slot=slot_nr,
                    pos=pos,
                    pid=pid,
                    product_name=prod_name,
                    buy_time=buy_time,
                )
            )

        self.slots.sort(key=lambda s: s.slot)

    async def fill(
        self,
        stock_service: StockService,
        farmis: list[SushiFarmi],
        recipes: dict[int, SushiRecipe],
        auto_train: bool = False,
        update_callback: Any | None = None,
    ) -> int:
        """Fill empty conveyor belt slots from warehouse stock (only if auto_train is True)."""
        if not auto_train:
            return 0

        free_slots = [s for s in self.slots if s.is_empty]
        if not free_slots:
            return 0

        filled_count = 0
        # Determine candidate products in warehouse
        candidate_pids = [
            pid
            for pid in recipes
            if stock_service.get_farm_amount(8, pid) > 0
            and not any(s.pid == pid for s in self.slots if s.pid)
        ]

        # Prioritize products matching farmis' missing needs
        needed_cats = set()
        for f in farmis:
            for cat, amt in f.need.items():
                if f.have.get(cat, 0) < amt:
                    needed_cats.add(cat)

        candidate_pids.sort(
            key=lambda pid: 0 if recipes[pid].category in needed_cats else 1
        )

        for slot in free_slots:
            if not candidate_pids:
                break
            target_pid = candidate_pids.pop(0)
            logger.info(
                f"Sushi-Bar: Belade Laufband-Slot {slot.slot} mit {recipes[target_pid].name} (PID {target_pid})..."
            )
            try:
                res = await self.client.api_call(
                    "farm",
                    {
                        "mode": "sushibar_settrainslot",
                        "slot": slot.slot,
                        "pid": target_pid,
                    },
                )
                filled_count += 1
                stock_service.deduct_stock(target_pid, 1, farm_id=8)
                if update_callback and isinstance(res, dict):
                    await update_callback(res)
            except Exception as e:  # noqa: BLE001
                logger.error(f"Sushi-Bar: Fehler beim Beladen von Laufband-Slot {slot.slot}: {e}")

        return filled_count
