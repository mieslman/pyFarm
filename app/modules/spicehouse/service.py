from typing import Any
from loguru import logger

from app.core.client import MFFGameClient
from app.modules.spicehouse.customers import SpiceCustomerService
from app.modules.spicehouse.mill import SpiceMillService
from app.modules.spicehouse.models import SpicehouseConfig, SpicehouseState
from app.modules.spicehouse.oven import SpiceOvenService
from app.modules.spicehouse.solver import SpiceQuestSolver
from app.services.stock_service import StockService


class SpicehouseService:
    """Coordinates all Gewürzhaus operations on Farm 10 (Oven, Mills, Customers & Quests)."""

    def __init__(
        self,
        client: MFFGameClient,
        farm_id: int = 10,
        position: int = 2,
        config: SpicehouseConfig | None = None,
    ):
        self.client = client
        self.farm_id = farm_id
        self.position = position
        self.config = config or SpicehouseConfig()

        self.oven = SpiceOvenService(client, farm_id=farm_id)
        self.mill = SpiceMillService(client, farm_id=farm_id)
        self.customer = SpiceCustomerService(client, farm_id=farm_id)
        self.solver = SpiceQuestSolver(farm_id=farm_id)

        self.level: int = 1
        self.points: int = 0

    @property
    def state(self) -> SpicehouseState:
        """Get snapshot of current Gewürzhaus state."""
        return SpicehouseState(
            level=self.level,
            points=self.points,
            oven_remain=self.oven.oven_remain,
            oven_is_ready=self.oven.oven_is_ready,
            oven_is_idle=self.oven.oven_is_idle,
            oven_lines=self.oven.lines,
            mill_slots=self.mill.slots,
            customers=self.customer.customers,
        )

    def update(self, spicehouse_data: dict[str, Any], catalog: dict[int, Any] | None = None):
        """Parse raw spicehouse payload from updateblock.spicehouse or spicehouse_init."""
        if not spicehouse_data or not isinstance(spicehouse_data, dict):
            return

        data_block = spicehouse_data.get("data", {})
        config_block = spicehouse_data.get("config", {})

        if isinstance(data_block, dict):
            self.level = int(data_block.get("level", 1))
            self.points = int(data_block.get("points", 0))

            # 1. Update Oven
            oven_data = data_block.get("oven")
            oven_slots = data_block.get("oven_slots")
            oven_levels = config_block.get("oven_levels") if isinstance(config_block, dict) else None
            self.oven.update(oven_data, oven_slots, oven_levels)

            # 2. Update Mills
            mill_slots = data_block.get("mill_slots")
            mill_levels = config_block.get("mill_levels") if isinstance(config_block, dict) else None
            server_time = int(data_block.get("time", 0)) if "time" in data_block else 0
            products_config = config_block.get("products") if isinstance(config_block, dict) else None
            self.mill.update(
                mill_slots,
                mill_levels,
                server_time=server_time,
                products_config=products_config,
            )

            # 3. Update Customers
            customers_data = data_block.get("customers")
            self.customer.update(customers_data)

    async def init_remote(self, catalog: dict[int, Any] | None = None) -> bool:
        """Call farm.php?mode=spicehouse_init to refresh server state."""
        try:
            res = await self.client.api_call("farm", {"mode": "spicehouse_init"})
            updateblock = res.get("updateblock", {})
            spicehouse_data = updateblock.get("spicehouse", {}) or res.get("spicehouse", {})
            if spicehouse_data:
                self.update(spicehouse_data, catalog=catalog)
                return True
        except Exception as e:
            logger.warning(f"Gewürzhaus: Fehler bei spicehouse_init: {e}")
        return False

    async def serve(
        self,
        stock_service: StockService,
        quest_status_main: dict[str, Any] | None = None,
        quest_requirements: dict[int, int] | None = None,
        catalog: dict[int, Any] | None = None,
    ):
        """Execute one complete service cycle for the Gewürzhaus:
        1. Fetch remote state (spicehouse_init).
        2. Harvest & load Trockenofen (prioritizing Quest 6, then largest surplus).
        3. Harvest & load Gewürzmühlen (prioritizing Quest 6, then largest dried stock).
        4. Serve satisfied customers (Farmis).
        """
        if not self.config.enabled:
            logger.debug("Gewürzhaus: Deaktiviert in Konfiguration.")
            return

        logger.info("========== Gewürzhaus: Starte Bewirtschaftungszyklus ==========")
        await self.init_remote(catalog=catalog)

        # Extract active demands for Questreihe 6
        quest_demands = self.solver.extract_quest6_demands(
            quest_status_main=quest_status_main,
            quest_requirements=quest_requirements,
        )
        if quest_demands:
            logger.debug(f"Gewürzhaus: Aktive Quest-6-Bedarfe: {quest_demands}")

        # 1. Trockenofen
        if self.config.auto_oven:
            await self.oven.harvest()
            await self.oven.produce(
                stock_service=stock_service,
                quest_solver=self.solver,
                quest_demands=quest_demands,
                min_crop_reserve=self.config.min_crop_reserve,
            )

        # 2. Gewürzmühlen
        if self.config.auto_mill:
            await self.mill.harvest(stock_service=stock_service)
            await self.mill.produce(
                stock_service=stock_service,
                quest_solver=self.solver,
                quest_demands=quest_demands,
            )

        # 3. Kunden
        if self.config.auto_customer:
            await self.customer.collect(stock_service=stock_service)

        logger.info("========== Gewürzhaus: Zyklus abgeschlossen ==========")
