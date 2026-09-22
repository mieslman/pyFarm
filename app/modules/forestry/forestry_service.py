from typing import Any

from loguru import logger

from app.config import ForestryConfig, settings
from app.core.client import MFFGameClient
from app.models.forestry import ForestryProduct
from app.modules.forestry.factory import Carpentry, Sawmill
from app.modules.forestry.farmis import ForestryFarmis
from app.modules.forestry.forrest import Forrest


class ForestryService:
    """Coordinates all operations in the forestry area (Baumerei)."""

    def __init__(self, client: MFFGameClient, config: ForestryConfig | None = None):
        self.client = client
        self.config = config or settings.forestry
        self.forrest = Forrest(client)
        self.sawmill = Sawmill(client)
        self.carpentry = Carpentry(client)
        self.farmis = ForestryFarmis(client)
        self.products: dict[int, ForestryProduct] = {}

    def parse_products_catalog(self, raw_catalog: dict[str, Any]) -> dict[int, ForestryProduct]:
        """Extract product metadata from datablock[4]."""
        if not isinstance(raw_catalog, dict):
            return self.products

        for stock_data in raw_catalog.values():
            if not isinstance(stock_data, dict):
                continue
            for pid_str, p_data in stock_data.items():
                if not str(pid_str).isdigit() or not isinstance(p_data, (list, tuple)):
                    continue
                pid = int(pid_str)
                # Structure: [0:?, 1:?, 2:harvest, 3:stockId, 4:?, 5:produces, 6:reqProducts, 7:?, 8:?, 9:name, ...]
                harvest = int(p_data[2]) if len(p_data) > 2 and str(p_data[2]).isdigit() else 1
                stock_id = int(p_data[3]) if len(p_data) > 3 and str(p_data[3]).isdigit() else 1
                produces = (
                    int(p_data[5])
                    if len(p_data) > 5 and str(p_data[5]).isdigit() and int(p_data[5]) > 0
                    else None
                )
                name = str(p_data[9]) if len(p_data) > 9 else f"Holzprodukt {pid}"

                req_prods: list[tuple[int, int]] = []
                if len(p_data) > 6 and p_data[6] != 0 and isinstance(p_data[6], list):
                    for req in p_data[6]:
                        if isinstance(req, (list, tuple)) and len(req) >= 2:
                            req_prods.append((int(req[0]), int(req[1])))
                elif (
                    len(p_data) > 11
                    and p_data[11] != 0
                    and str(p_data[11]).isdigit()
                    and int(p_data[11]) > 0
                ):
                    req_prods.append((int(p_data[11]), 1))

                self.products[pid] = ForestryProduct(
                    pid=pid,
                    name=name,
                    category=stock_id,
                    amount=0,
                    harvest=harvest,
                    produces=produces,
                    required_products=req_prods,
                )

        return self.products

    def update_stock_amounts(self, forestry_stock: dict[str, Any]):
        """Update inventory amounts from updateblock.forestry_stock."""
        if not isinstance(forestry_stock, dict):
            return

        for pid_str, amount_val in forestry_stock.items():
            if str(pid_str).isdigit():
                pid = int(pid_str)
                try:
                    amt = int(amount_val)
                except (ValueError, TypeError):
                    amt = 0

                if pid in self.products:
                    self.products[pid].amount = amt
                else:
                    # Create placeholder product model if catalog not yet loaded
                    self.products[pid] = ForestryProduct(
                        pid=pid,
                        name=f"Holzprodukt {pid}",
                        category=1,
                        amount=amt,
                    )

    async def update(self, body: dict[str, Any] | None = None) -> dict[str, Any]:
        """Fetch current forestry status and update submodules."""
        if not body:
            body = await self.client.api_call("forestry", {"action": "initforestry"})

        datablock = body.get("datablock", [])
        if not datablock or datablock[0] != 1:
            logger.warning("Forst: initforestry lieferte ungültigen datablock.")
            return body

        # 1. Products catalog (datablock[4])
        if len(datablock) > 4 and isinstance(datablock[4], dict):
            self.parse_products_catalog(datablock[4])

        # 2. Update stock amounts (updateblock.forestry_stock or datablock)
        up_block = body.get("updateblock", {})
        if isinstance(up_block, dict) and "forestry_stock" in up_block:
            self.update_stock_amounts(up_block["forestry_stock"])

        # 3. Update Forrest (datablock[1])
        if len(datablock) > 1 and isinstance(datablock[1], list):
            self.forrest.update(datablock[1])

        # 4. Update Factories (datablock[2])
        if len(datablock) > 2 and isinstance(datablock[2], dict):
            facs = datablock[2]
            if "1" in facs:
                self.sawmill.update(facs["1"])
            if "2" in facs:
                self.carpentry.update(facs["2"])

        # 5. Update Farmis (datablock[5])
        if len(datablock) > 5 and isinstance(datablock[5], list):
            self.farmis.update(datablock[5])

        return body

    async def serve(self) -> dict[str, Any]:
        """Execute full automation cycle for the forestry area."""
        if not self.config.enabled:
            return {}

        logger.info("---------- ForestryService: Starte Zyklus ----------")
        body = await self.update()

        # 1. Serve Farmis immediately if products are available
        if self.config.serve_farmis:
            served = await self.farmis.serve_available(self.products)
            if served > 0:
                logger.info(f"Forst: {served} Kunde(n) an der Waldhütte bedient.")

        # 2. Harvest completed manufacturing slots
        harvested_sawmill = await self.sawmill.harvest()
        harvested_carpentry = await self.carpentry.harvest()
        if harvested_sawmill or harvested_carpentry:
            logger.info(
                f"Forst: {harvested_sawmill} Sägewerk- und {harvested_carpentry} Schreinerei-Slots abgeholt."
            )

        # 3. Produce new items in free manufacturing slots strictly based on Farmi demand
        if self.config.auto_produce:
            farmi_demands = self.farmis.get_missing_demands(self.products)

            # 3a. Carpentry: produce furniture requested by Farmis
            started_carp, carp_subs = await self.carpentry.produce_orders(
                self.products, farmi_demands
            )

            # 3b. Sawmill: produce boards requested directly by Farmis OR needed as ingredients by Carpentry
            sawmill_orders = {
                pid: amt
                for pid, amt in farmi_demands.items()
                if pid in self.products and self.products[pid].category == 3
            }
            for sub_pid, sub_amt in carp_subs.items():
                sawmill_orders[sub_pid] = sawmill_orders.get(sub_pid, 0) + sub_amt

            started_saw, _ = await self.sawmill.produce_orders(self.products, sawmill_orders)

            if started_saw or started_carp:
                logger.info(
                    f"Forst: {started_saw} Sägewerk- und {started_carp} Schreinerei-Produktionen gestartet."
                )
            else:
                logger.debug(
                    "Forst: Keine offenen Farmi-Aufträge für Schreinerei/Sägewerk vorhanden."
                )

        # 4. Forest lifecycle: Cut, Plant, Water
        if self.config.auto_cut:
            await self.forrest.cut()

        if self.config.auto_plant:
            await self.forrest.plant(self.products)

        if self.config.auto_water:
            await self.forrest.water()

        return body
