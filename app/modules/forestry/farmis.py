from typing import Any

from loguru import logger

from app.core.client import MFFGameClient
from app.models.forestry import FarmiOrder, ForestryProduct


class ForestryFarmis:
    """Manages customer visitors at the forestry hut."""

    def __init__(self, client: MFFGameClient):
        self.client = client
        self.farmis: list[FarmiOrder] = []

    def update(self, raw_farmis: list[dict[str, Any]]) -> list[FarmiOrder]:
        """Update farmis list from datablock[5]."""
        self.farmis = []
        if not isinstance(raw_farmis, list):
            return self.farmis

        for f_data in raw_farmis:
            if not isinstance(f_data, dict):
                continue
            farmi_id = int(f_data.get("farmiid", 0))
            pos = int(f_data.get("position", 0))
            pts = int(f_data.get("points", 0))
            money = float(f_data.get("price", 0.0))

            prods = {}
            for p_item in f_data.get("products", []):
                if isinstance(p_item, dict):
                    pid = int(p_item.get("product", 0))
                    amt = int(p_item.get("amount", 0))
                    if pid > 0 and amt > 0:
                        prods[pid] = prods.get(pid, 0) + amt

            self.farmis.append(
                FarmiOrder(
                    farmi_id=farmi_id,
                    position=pos,
                    products=prods,
                    reward_points=pts,
                    reward_money=money,
                )
            )
        return self.farmis

    async def serve_available(self, products: dict[int, ForestryProduct]) -> int:
        """Serve farmis immediately if all requested products are currently in stock."""
        served = 0
        for farmi in self.farmis:
            all_available = True
            for pid, needed in farmi.products.items():
                stock_prod = products.get(pid)
                if not stock_prod or stock_prod.amount < needed:
                    all_available = False
                    break

            if all_available:
                logger.info(
                    f"Forstwirt-Farmi {farmi.farmi_id} (Pos {farmi.position}): "
                    f"Alle Waren vorrätig. Bedienen für {farmi.reward_points} Punkte..."
                )
                res = await self.client.api_call(
                    "forestry",
                    {"action": "sellfarmi", "productid": farmi.farmi_id},
                )
                if res.get("datablock", [0])[0] == 1:
                    served += 1
                    # Deduct sold products from in-memory amounts
                    for pid, needed in farmi.products.items():
                        products[pid].amount -= needed
        return served

    def get_missing_demands(self, products: dict[int, ForestryProduct]) -> dict[int, int]:
        """Aggregate missing products needed across all waiting farmis: {pid: total_missing_amount}."""
        demands: dict[int, int] = {}
        for farmi in self.farmis:
            for pid, needed in farmi.products.items():
                stock_prod = products.get(pid)
                available = stock_prod.amount if stock_prod else 0
                if available < needed:
                    missing = needed - available
                    demands[pid] = demands.get(pid, 0) + missing
        return demands
