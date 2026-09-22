from typing import Any

from loguru import logger

from app.config import SushibarConfig, settings
from app.core.client import MFFGameClient
from app.modules.sushibar.farmis import SushiFarmiService
from app.modules.sushibar.kitchen import SushiKitchenService
from app.modules.sushibar.models import (
    SushiBarSummary,
    SushiRecipe,
)
from app.modules.sushibar.solver import SushiQuestSolver
from app.modules.sushibar.train import SushiTrainService
from app.services.stock_service import StockService


class SushiBarService:
    """Master orchestrator for the Sushi-Bar facility (Farm 8, Position 2)."""

    def __init__(
        self,
        client: MFFGameClient,
        farm_id: int = 8,
        position: int = 2,
        config: SushibarConfig | None = None,
    ):
        self.client = client
        self.farm_id = farm_id
        self.position = position
        self.config = config or settings.sushibar

        self.kitchen = SushiKitchenService(client)
        self.solver = SushiQuestSolver(client)
        self.train = SushiTrainService(client)
        self.farmis = SushiFarmiService(client)

        self.level: int = 1
        self.level_percent: float = 0.0
        self.level_rest: int = 0
        self.recipes: dict[int, SushiRecipe] = {}
        self.is_initialized: bool = False

    def update(self, sushibar_data: dict[str, Any], catalog: dict[int, Any] | None = None):
        """Update Sushi-Bar state from sushibar_init or getfarms data."""
        if not isinstance(sushibar_data, dict):
            return

        # 1. Parse core data
        data_block = sushibar_data.get("data", {})
        if isinstance(data_block, dict):
            self.level = int(data_block.get("level", self.level))
            self.level_percent = float(data_block.get("level_percent", 0.0))
            self.level_rest = int(data_block.get("level_rest", 0))

        # 2. Parse recipe configs
        cfg_block = sushibar_data.get("config", {})
        if isinstance(cfg_block, dict):
            raw_prods = cfg_block.get("products", {})
            if isinstance(raw_prods, dict):
                for pid_str, p_data in raw_prods.items():
                    if not str(pid_str).isdigit() or not isinstance(p_data, dict):
                        continue
                    pid = int(pid_str)
                    needs_dict: dict[int, int] = {}
                    for n_pid, n_amt in p_data.get("needs", {}).items():
                        if str(n_pid).isdigit():
                            needs_dict[int(n_pid)] = int(n_amt)

                    prod_name = catalog[pid].name if (catalog and pid in catalog) else f"PID {pid}"
                    self.recipes[pid] = SushiRecipe(
                        pid=pid,
                        name=prod_name,
                        level=int(p_data.get("level", 1)),
                        category=str(p_data.get("category", "sushi")),
                        cost_money=int(p_data.get("money", 0)),
                        cost_coins=int(p_data.get("coins", 0)),
                        duration=int(p_data.get("duration", 0)),
                        amount=int(p_data.get("amount", 1)),
                        points=int(p_data.get("points", 0)),
                        eattime=int(p_data.get("eattime", 0)),
                        needs=needs_dict,
                        reward=p_data.get("reward", {}),
                    )

        # 3. Update Kitchen Slots
        raw_prod = sushibar_data.get("production", {})
        raw_slots = data_block.get("slots", {}) if isinstance(data_block, dict) else {}
        self.kitchen.update_slots(raw_prod, raw_slots, self.recipes, catalog)

        # 4. Update Conveyor Train Slots
        raw_train = data_block.get("train", {}) if isinstance(data_block, dict) else {}
        self.train.update_slots(raw_train, self.recipes, catalog)

        # 5. Update Farmis
        raw_farmis = sushibar_data.get("farmis", {})
        self.farmis.update_farmis(raw_farmis, catalog)

        self.is_initialized = True

    async def init_remote(self, catalog: dict[int, Any] | None = None):
        """Fetch fresh state via farm.php mode=sushibar_init."""
        try:
            res = await self.client.api_call("farm", {"mode": "sushibar_init"})
            ub = res.get("updateblock", {}) if isinstance(res, dict) else {}
            s_data = ub.get("sushibar", {})
            if s_data:
                self.update(s_data, catalog)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Sushi-Bar: Fehler beim Initialisieren via sushibar_init: {e}")

    async def serve(
        self,
        stock_service: StockService,
        quest_status_main: dict[str, Any] | None = None,
        catalog: dict[int, Any] | None = None,
    ):
        """Execute one complete Sushi-Bar automation cycle."""
        if not self.config.enabled:
            logger.debug("Sushi-Bar: Deaktiviert in Konfiguration, überspringe.")
            return

        logger.info("========== Sushi-Bar: Starte Bewirtschaftungszyklus ==========")

        # Ensure we have fresh status
        await self.init_remote(catalog)

        async def refresh_callback(body: dict[str, Any]):
            stock_service.update(body)
            ub = body.get("updateblock", {}) if isinstance(body, dict) else {}
            s_data = ub.get("sushibar", {})
            if s_data:
                self.update(s_data, catalog)

        # 1. Harvest ready kitchen slots
        if self.config.auto_harvest:
            harvested = await self.kitchen.harvest(update_callback=refresh_callback)
            if harvested > 0:
                logger.info(f"Sushi-Bar: {harvested} Kochslot(s) erfolgreich abgeerntet.")

        # 2. Collect satisfied Farmis
        if self.config.auto_farmi:
            collected = await self.farmis.collect(
                auto_farmi=self.config.auto_farmi, update_callback=refresh_callback
            )
            if collected > 0:
                logger.info(f"Sushi-Bar: {collected} Gast/Gäste erfolgreich abkassiert.")

        # 3. Fill conveyor belt train (if enabled, default: False)
        if self.config.auto_train:
            filled = await self.train.fill(
                stock_service=stock_service,
                farmis=self.farmis.farmis,
                recipes=self.recipes,
                auto_train=self.config.auto_train,
                update_callback=refresh_callback,
            )
            if filled > 0:
                logger.info(f"Sushi-Bar: {filled} Laufband-Slot(s) beladen.")

        # 4. Produce according to Questreihe 5
        if self.config.auto_produce:
            current_quest_id = 1
            if quest_status_main and "5" in quest_status_main:
                q5_info = quest_status_main["5"]
                if isinstance(q5_info, dict) and "questid" in q5_info:
                    current_quest_id = int(q5_info["questid"])

            started = await self.kitchen.produce(
                stock_service=stock_service,
                quest_solver=self.solver,
                sushibar_level=self.level,
                recipes=self.recipes,
                catalog=catalog,
                current_quest_id=current_quest_id,
                strategy=self.config.production_strategy,
                preferred_pids=self.config.preferred_pids,
                reserve_full_field=self.config.reserve_full_field,
                coin_protection=self.config.coin_protection,
                update_callback=refresh_callback,
            )
            if started > 0:
                logger.info(f"Sushi-Bar: {started} neue Zubereitung(en) gestartet.")

        logger.info("========== Sushi-Bar: Zyklus abgeschlossen ==========")

    def get_summary(self) -> SushiBarSummary:
        """Return summary DTO for API clients and dashboard."""
        return SushiBarSummary(
            level=self.level,
            level_percent=self.level_percent,
            level_rest=self.level_rest,
            farm=self.farm_id,
            position=self.position,
            production_slots=self.kitchen.slots,
            train_slots=self.train.slots,
            farmis=self.farmis.farmis,
            quest5_target=self.solver.last_target,
            enabled=self.config.enabled,
            auto_harvest=self.config.auto_harvest,
            auto_produce=self.config.auto_produce,
            auto_train=self.config.auto_train,
            auto_farmi=self.config.auto_farmi,
            production_strategy=self.config.production_strategy,
        )
