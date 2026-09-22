from typing import Any

from loguru import logger

from app.core.client import MFFGameClient
from app.models.forestry import ForestryProduct, ProductionSlot


class Factory:
    """Base class for forestry manufacturing facilities (Sawmill and Carpentry)."""

    def __init__(self, client: MFFGameClient, building_id: int, name: str, stock_category: int):
        self.client = client
        self.building_id = building_id
        self.name = name
        self.stock_category = stock_category
        self.slots: dict[int, ProductionSlot] = {
            1: ProductionSlot(slot_id=1),
            2: ProductionSlot(slot_id=2),
        }

    def update(self, building_data: dict[str, Any]) -> dict[int, ProductionSlot]:
        """Update slots from datablock[2][building_id]."""
        raw_slots = building_data.get("slots", {}) if isinstance(building_data, dict) else {}
        self.slots = {1: ProductionSlot(slot_id=1), 2: ProductionSlot(slot_id=2)}

        for slot_id_str, slot_data in raw_slots.items():
            if not str(slot_id_str).isdigit():
                continue
            slot_id = int(slot_id_str)
            if isinstance(slot_data, dict):
                pid_raw = slot_data.get("productid")
                pid = int(pid_raw) if pid_raw and str(pid_raw).isdigit() else None
                rem = int(slot_data.get("remain", 0))
                rdy = slot_data.get("ready")
                self.slots[slot_id] = ProductionSlot(
                    slot_id=slot_id,
                    productid=pid,
                    remain=rem,
                    ready=rdy,
                    busy=True,
                )
            else:
                self.slots[slot_id] = ProductionSlot(slot_id=slot_id, busy=False)

        return self.slots

    async def harvest(self) -> int:
        """Collect finished productions from all busy slots with remain <= 0."""
        harvested_count = 0
        for slot in self.slots.values():
            if slot.is_finished:
                logger.info(
                    f"{self.name}: Ernte Erzeugnis aus Slot {slot.slot_id} (PID {slot.productid})..."
                )
                res = await self.client.api_call(
                    "forestry",
                    {
                        "action": "cropproduction",
                        "position": self.building_id,
                        "slot": slot.slot_id,
                    },
                )
                if res.get("datablock", [0])[0] == 1:
                    harvested_count += 1
                    slot.busy = False
                    slot.productid = None
        return harvested_count

    async def start_production(self, slot_id: int, pid: int) -> bool:
        """Start a production in a given slot."""
        logger.info(f"{self.name}: Starte Produktion in Slot {slot_id} für PID {pid}...")
        res = await self.client.api_call(
            "forestry",
            {
                "action": "startproduction",
                "position": self.building_id,
                "slot": slot_id,
                "productid": pid,
            },
        )
        success = res.get("datablock", [0])[0] == 1
        if success and slot_id in self.slots:
            self.slots[slot_id].busy = True
            self.slots[slot_id].productid = pid
        return success

    async def produce_orders(
        self,
        products: dict[int, ForestryProduct],
        orders: dict[int, int],
    ) -> tuple[int, dict[int, int]]:
        """Produce items strictly for requested orders.

        Returns:
            (started_count, sub_requirements)
            sub_requirements contains ingredients needed from upstream factories (e.g. sawmill boards).
        """
        started = 0
        sub_requirements: dict[int, int] = {}
        free_slots = [s for s in self.slots.values() if not s.busy]
        if not free_slots or not orders:
            return 0, sub_requirements

        # Copy orders to track remaining missing units
        pending_orders = {pid: amt for pid, amt in orders.items() if amt > 0}

        # Deduct items already in production in this factory's slots
        for slot in self.slots.values():
            if slot.busy and slot.productid in pending_orders:
                pending_orders[slot.productid] = max(0, pending_orders[slot.productid] - 1)

        # Candidates: products of this factory's category that have pending orders
        candidates = [
            products[pid]
            for pid, amt in pending_orders.items()
            if pid in products and products[pid].category == self.stock_category and amt > 0
        ]
        # Sort candidates by lowest stock amount
        candidates.sort(key=lambda p: p.amount)

        for slot in free_slots:
            if slot.busy:
                continue

            produced_in_slot = False
            for prod in candidates:
                if pending_orders.get(prod.pid, 0) <= 0:
                    continue

                ingredients = prod.required_products
                if not ingredients:
                    continue

                can_produce = True
                missing_ingredients: dict[int, int] = {}
                for ing_pid, ing_amount in ingredients:
                    available = products.get(ing_pid)
                    avail_amt = available.amount if available else 0
                    if avail_amt < ing_amount:
                        can_produce = False
                        missing_ingredients[ing_pid] = ing_amount - avail_amt

                if can_produce:
                    ok = await self.start_production(slot.slot_id, prod.pid)
                    if ok:
                        started += 1
                        produced_in_slot = True
                        pending_orders[prod.pid] -= 1
                        # Deduct used ingredients from local in-memory count
                        for ing_pid, ing_amount in ingredients:
                            products[ing_pid].amount -= ing_amount
                        break
                else:
                    # Accumulate missing ingredients needed from upstream (e.g. sawmill)
                    for m_pid, m_amt in missing_ingredients.items():
                        sub_requirements[m_pid] = sub_requirements.get(m_pid, 0) + m_amt

            if not produced_in_slot:
                # No candidate could be produced for this free slot
                continue

        return started, sub_requirements

    async def produce_available(
        self,
        products: dict[int, ForestryProduct],
        priority_pids: list[int] | None = None,
    ) -> int:
        """Produce items in free slots (backward compatible helper)."""
        if priority_pids:
            orders = {pid: 99 for pid in priority_pids}
            started, _ = await self.produce_orders(products, orders)
            return started

        category_prods = [p for p in products.values() if p.category == self.stock_category]
        category_prods.sort(key=lambda p: p.amount)
        orders = {p.pid: 99 for p in category_prods}
        started, _ = await self.produce_orders(products, orders)
        return started


class Sawmill(Factory):
    """Sägewerk: processes trunks (cat 2) into sawn wood / boards (cat 3)."""

    def __init__(self, client: MFFGameClient):
        super().__init__(client=client, building_id=1, name="Sägewerk", stock_category=3)


class Carpentry(Factory):
    """Schreinerei: processes sawn wood (cat 3) and trunks (cat 2) into furniture (cat 4)."""

    def __init__(self, client: MFFGameClient):
        super().__init__(client=client, building_id=2, name="Schreinerei", stock_category=4)
