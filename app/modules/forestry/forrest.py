from typing import Any

from loguru import logger

from app.core.client import MFFGameClient
from app.models.forestry import ForestryProduct, TreeSlot


class Forrest:
    """Manages the 25 tree plots in the forestry area."""

    def __init__(self, client: MFFGameClient):
        self.client = client
        self.trees: list[TreeSlot] = [TreeSlot(position=i + 1) for i in range(25)]

    def update(self, raw_trees: list[dict[str, Any]]) -> list[TreeSlot]:
        """Update the 25 tree positions from the raw datablock[1] array."""
        self.trees = [TreeSlot(position=i + 1) for i in range(25)]

        for tree_data in raw_trees:
            if not isinstance(tree_data, dict):
                continue
            pos = int(tree_data.get("position", 0))
            if 1 <= pos <= 25:
                pid_raw = tree_data.get("productid")
                pid = int(pid_raw) if pid_raw and str(pid_raw).isdigit() else None
                rem = int(tree_data.get("remain", 0))
                w_rem = int(tree_data.get("waterremain", 0))
                rdy = tree_data.get("ready")

                self.trees[pos - 1] = TreeSlot(
                    position=pos,
                    productid=pid,
                    remain=rem,
                    waterremain=w_rem,
                    ready=rdy,
                )

        return self.trees

    @property
    def has_ready_trees(self) -> bool:
        return any(t.is_ready_to_cut for t in self.trees)

    @property
    def has_empty_slots(self) -> bool:
        return any(t.is_empty for t in self.trees)

    @property
    def needs_watering(self) -> bool:
        return any(t.needs_water for t in self.trees)

    async def cut(self) -> bool:
        """Cut all ripe trees if any remain <= 0."""
        if not self.has_ready_trees:
            return False

        logger.info("Forst: Reife Bäume schlagreif. Fülle alle Bäume (action=cropall)...")
        res = await self.client.api_call("forestry", {"action": "cropall"})
        return res.get("datablock", [0])[0] == 1

    async def plant(self, products: dict[int, ForestryProduct]) -> int | None:
        """Automatically replant empty tree plots with the seedling of the lowest-stock trunk."""
        if not self.has_empty_slots:
            return None

        # Find trunks (category 2) sorted ascending by amount in stock
        trunks = [p for p in products.values() if p.category == 2]
        if not trunks:
            logger.warning("Forst: Keine Rohholz-Stammprodukte im Katalog gefunden.")
            return None

        trunks.sort(key=lambda t: t.amount)
        lowest_trunk = trunks[0]

        # Find the seedling (category 1) that produces this trunk
        seedlings = [
            p for p in products.values() if p.category == 1 and p.produces == lowest_trunk.pid
        ]
        if not seedlings:
            logger.warning(
                f"Forst: Kein Setzling für Stamm PID {lowest_trunk.pid} ({lowest_trunk.name}) gefunden."
            )
            return None

        chosen_seedling = seedlings[0]
        logger.info(
            f"Forst: Bepflanze freie Baumplätze mit '{chosen_seedling.name}' "
            f"(Geringster Stammbestand: {lowest_trunk.amount}x '{lowest_trunk.name}')..."
        )

        res = await self.client.api_call(
            "forestry", {"action": "autoplant", "productid": chosen_seedling.pid}
        )
        if res.get("datablock", [0])[0] == 1:
            return chosen_seedling.pid
        return None

    async def water(self) -> bool:
        """Water all trees in the forest."""
        if not self.needs_watering:
            return False

        logger.info("Forst: Bewässere Waldfläche (action=water)...")
        res = await self.client.api_call("forestry", {"action": "water"})
        return res.get("datablock", [0])[0] == 1
