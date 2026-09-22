from typing import Any

from loguru import logger

from app.config import FarmStrategyConfig, settings
from app.core.client import MFFGameClient
from app.modules.agriculture.field import Field
from app.modules.agriculture.strategies import PlantStrategySolver
from app.modules.agriculture.vehicle import VehicleService
from app.modules.farm_buildings.factory import Factory
from app.modules.farm_buildings.fuelstation import Fuelstation
from app.modules.farm_buildings.shed import Shed
from app.modules.spicehouse.service import SpicehouseService
from app.modules.sushibar.service import SushiBarService
from app.modules.sushibar.solver import SushiQuestSolver
from app.services.stock_service import StockService


class FarmService:
    """Orchestrates all farm buildings across all farms (Fields, Sheds, Factories, Vehicles)."""

    def __init__(
        self,
        client: MFFGameClient,
        config: FarmStrategyConfig | None = None,
    ):
        self.client = client
        self.config = config or settings.agriculture
        self.fields: list[Field] = []
        self.sheds: list[Shed] = []
        self.factories: list[Factory] = []
        self.fuelstations: list[Fuelstation] = []
        self.sushibars: list[SushiBarService] = []
        self.spicehouses: list[SpicehouseService] = []
        self.vehicle_service: VehicleService = VehicleService(client, config=settings.vehicles)
        self.raw_farms: dict[int, dict[int, Any]] = {}

    async def update_farms(
        self,
        body: dict[str, Any] | None = None,
        catalog: dict[int, Any] | None = None,
    ):
        """Discover buildings on all farms via getfarms and initialize Field, Shed, Fuelstation and SushiBar handlers."""
        if not body:
            body = await self.client.api_call(
                "farm", {"mode": "getfarms", "farm": 1, "position": 0}
            )

        updateblock = body.get("updateblock", {})
        farms_block = updateblock.get("farms", {})
        raw_farms = farms_block.get("farms", {})
        farms_config = farms_block.get("config", {})

        # Parse sheds mapping from building2product
        shed_building_ids = {"2", "3", "4", "5", "6", "12"}
        b2p = farms_config.get("building2product", {})
        if isinstance(b2p, dict):
            for bid in b2p:
                shed_building_ids.add(str(bid))

        # Parse factories mapping (advancedbuilding + specific factory IDs)
        factory_building_ids = {"7", "8", "9", "10", "13", "14", "16", "25"}
        adv = farms_config.get("advancedbuilding", {})
        if isinstance(adv, dict):
            for bid in adv:
                factory_building_ids.add(str(bid))

        self.fields = []
        self.sheds = []
        self.factories = []
        self.fuelstations = []
        self.sushibars = []
        self.spicehouses = []

        if isinstance(raw_farms, dict):
            for farm_id_str, farm_buildings in raw_farms.items():
                if not str(farm_id_str).isdigit() or not isinstance(farm_buildings, dict):
                    continue
                farm_id = int(farm_id_str)

                for pos_str, b_data in farm_buildings.items():
                    if not str(pos_str).isdigit() or not isinstance(b_data, dict):
                        continue
                    pos = int(pos_str)
                    building_id = str(b_data.get("buildingid", "0"))
                    b_name = str(b_data.get("name", "Gebäude"))

                    # Building ID 1 = Acker (Field)
                    if building_id == "1":
                        self.fields.append(
                            Field(
                                client=self.client,
                                farm_id=farm_id,
                                position=pos,
                                name=b_name or "Acker",
                                harvest_mode=self.config.harvest_mode,
                                auto_water=self.config.auto_water,
                                auto_crop=self.config.auto_crop,
                            )
                        )
                    # Building ID 20 = Biosprit-Anlage (Fuelstation)
                    elif building_id == "20":
                        fs = Fuelstation(
                            client=self.client,
                            farm_id=farm_id,
                            position=pos,
                        )
                        fs.update(b_data)
                        self.fuelstations.append(fs)
                    # Building ID 23 = Sushi-Bar
                    elif building_id == "23":
                        sb = SushiBarService(
                            client=self.client,
                            farm_id=farm_id,
                            position=pos,
                        )
                        sushibar_data = updateblock.get("sushibar", {})
                        if sushibar_data:
                            sb.update(sushibar_data, catalog=catalog)
                        self.sushibars.append(sb)
                    # Building ID 24 = Gewürzhaus (Farm 10)
                    elif building_id == "24":
                        sh = SpicehouseService(
                            client=self.client,
                            farm_id=farm_id,
                            position=pos,
                            config=settings.spicehouse,
                        )
                        spicehouse_data = updateblock.get("spicehouse", {})
                        if spicehouse_data:
                            sh.update(spicehouse_data, catalog=catalog)
                        self.spicehouses.append(sh)
                    # Sheds (Tierställe)
                    elif building_id in shed_building_ids:
                        self.sheds.append(
                            Shed(
                                client=self.client,
                                farm_id=farm_id,
                                position=pos,
                                building_id=int(building_id),
                                name=b_name or "Stall",
                            )
                        )
                    # Factories (Veredelungsbetriebe: Käserei, Ölpresse, Spinnerei, Strickerei, etc.)
                    elif building_id in factory_building_ids:
                        self.factories.append(
                            Factory(
                                client=self.client,
                                farm_id=farm_id,
                                position=pos,
                                building_id=int(building_id),
                                name=b_name or None,
                            )
                        )

        # Update vehicles from updateblock.map
        map_data = updateblock.get("map", {})
        if map_data:
            self.vehicle_service.update(map_data)

        logger.info(
            f"FarmService: {len(self.fields)} Äcker, {len(self.sheds)} Ställe, "
            f"{len(self.factories)} Fabrik(en), {len(self.fuelstations)} Biosprit-Anlage(n), "
            f"{len(self.sushibars)} Sushi-Bar(s) und {len(self.spicehouses)} Gewürzhaus/häuser aufgefunden."
        )


    async def loop(
        self,
        stock_service: StockService,
        quest_requirements: dict[int, int] | None = None,
        quest_status_main: dict[str, Any] | None = None,
        catalog: dict[int, Any] | None = None,
    ):
        """Execute one complete serving cycle across all fields, sheds, fuelstations and sushibar."""
        logger.info("========== FarmService: Starte Zyklus ==========")
        await self.update_farms(catalog=catalog)

        # Resolve Quest 5 water requirements for Farm 8 if needed
        quest5_water_candidates = None
        current_q5_id: int | None = None
        solver: SushiQuestSolver | None = None
        recipes: dict[int, Any] = {}

        if quest_status_main and "5" in quest_status_main:
            q5_info = quest_status_main["5"]
            if isinstance(q5_info, dict) and "questid" in q5_info:
                try:
                    current_q5_id = int(q5_info["questid"])
                except (ValueError, TypeError):
                    current_q5_id = 1

        sb = self.sushibars[0] if self.sushibars else None
        solver = sb.solver if sb else SushiQuestSolver(self.client)
        recipes = sb.recipes if sb else {}

        has_farm8_fields = any(f.farm_id == 8 for f in self.fields)
        if has_farm8_fields and "quest" in self.config.plant_strategy.lower() and current_q5_id:
            try:
                if sb and not recipes:
                    await sb.init_remote(catalog)
                    recipes = sb.recipes

                quest5_water_candidates = await solver.get_quest5_water_requirements(
                    current_quest_id=current_q5_id,
                    recipes=recipes,
                    stock_service=stock_service,
                    catalog=catalog,
                    min_products=self.config.min_products,
                )
                if quest5_water_candidates:
                    logger.info(
                        f"FarmService: {len(quest5_water_candidates)} Quest-5-Bedarfe für Farm 8 identifiziert "
                        f"(Top: PID {quest5_water_candidates[0][0]}, {quest5_water_candidates[0][3]})."
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"FarmService: Konnte Quest-5-Bedarfe für Farm 8 nicht ermitteln: {e}")

        # 1. Äcker bewirtschaften
        for field in self.fields:
            try:
                fixed_pid = self.config.farm_crops.get(field.farm_id)
                farm_category = self.config.get_farm_category(field.farm_id)
                plant_candidate = PlantStrategySolver.resolve_candidate(
                    strategy_name=self.config.plant_strategy,
                    stock_service=stock_service,
                    category=farm_category,
                    min_products=self.config.min_products,
                    quest_requirements=quest_requirements,
                    fixed_pid=fixed_pid,
                    farm_id=field.farm_id,
                    quest5_water_candidates=quest5_water_candidates if field.farm_id == 8 else None,
                )
                await field.serve(plant_candidate)
            except Exception as e:  # noqa: BLE001
                logger.opt(exception=True).error(
                    f"FarmService: Fehler bei Feld {field.farm_id}/{field.position}: {e}"
                )

        # 2. Tierställe versorgen
        for shed in self.sheds:
            try:
                await shed.serve(stock_service)
            except Exception as e:  # noqa: BLE001
                logger.opt(exception=True).error(
                    f"FarmService: Fehler bei Stall {shed.farm_id}/{shed.position}: {e}"
                )

        # 3. Biosprit-Anlage(n) versorgen
        for fuelstation in self.fuelstations:
            try:
                await fuelstation.serve(stock_service)
            except Exception as e:  # noqa: BLE001
                logger.opt(exception=True).error(
                    f"FarmService: Fehler bei Biosprit-Anlage {fuelstation.farm_id}/{fuelstation.position}: {e}"
                )

        # 4. Sushi-Bar(s) versorgen
        for sushibar in self.sushibars:
            try:
                await sushibar.serve(
                    stock_service=stock_service,
                    quest_status_main=quest_status_main,
                    catalog=catalog,
                )
            except Exception as e:  # noqa: BLE001
                logger.opt(exception=True).error(
                    f"FarmService: Fehler bei Sushi-Bar {sushibar.farm_id}/{sushibar.position}: {e}"
                )

        # 5. Veredelungsfabriken versorgen
        for factory in self.factories:
            try:
                await factory.serve(
                    stock_service=stock_service,
                    quest_status_main=quest_status_main,
                    quest_requirements=quest_requirements,
                    catalog=catalog,
                )
            except Exception as e:  # noqa: BLE001
                logger.opt(exception=True).error(
                    f"FarmService: Fehler bei Fabrik {factory.name} {factory.farm_id}/{factory.position}: {e}"
                )

        # 6. Gewürzhaus versorgen (Farm 10)
        for spicehouse in self.spicehouses:
            try:
                await spicehouse.serve(
                    stock_service=stock_service,
                    quest_status_main=quest_status_main,
                    quest_requirements=quest_requirements,
                    catalog=catalog,
                )
            except Exception as e:  # noqa: BLE001
                logger.opt(exception=True).error(
                    f"FarmService: Fehler bei Gewürzhaus {spicehouse.farm_id}/{spicehouse.position}: {e}"
                )

        # 7. Fahrzeug-Logistik abwickeln
        if settings.vehicles.enabled:
            try:
                logistics_quest_reqs = dict(quest_requirements) if quest_requirements else {}
                quest_priority_order: dict[int, int] = {}

                # Register general main quests order
                if quest_status_main and isinstance(quest_status_main, dict):
                    for q_info in quest_status_main.values():
                        if isinstance(q_info, dict) and "questid" in q_info:
                            try:
                                qid = int(q_info["questid"])
                                for req_key in ("reqs", "products", "needs"):
                                    if req_key in q_info and isinstance(q_info[req_key], dict):
                                        for r_pid in q_info[req_key]:
                                            if str(r_pid).isdigit():
                                                pid_int = int(r_pid)
                                                if (
                                                    pid_int not in quest_priority_order
                                                    or qid < quest_priority_order[pid_int]
                                                ):
                                                    quest_priority_order[pid_int] = qid
                            except (ValueError, TypeError):
                                pass

                # Register Questreihe 5 water demands order (e.g. Q66 Taro-Wurzel, Q68 Brunnenkresse)
                if solver and current_q5_id:
                    quest5_logistics_candidates = await solver.get_quest5_water_requirements(
                        current_quest_id=current_q5_id,
                        recipes=recipes,
                        stock_service=stock_service,
                        catalog=catalog,
                        min_products=self.config.min_products,
                        for_logistics=True,
                    )
                    for pid, deficit, q_id, reason in quest5_logistics_candidates:
                        if deficit > 0:
                            current_main = stock_service.get_amount(pid)
                            logistics_quest_reqs[pid] = max(
                                logistics_quest_reqs.get(pid, 0),
                                current_main + deficit,
                            )
                            if pid not in quest_priority_order or q_id < quest_priority_order[pid]:
                                quest_priority_order[pid] = q_id

                await self.vehicle_service.loop(
                    stock_service=stock_service,
                    quest_requirements=logistics_quest_reqs,
                    quest_priority_order=quest_priority_order,
                    sushibar_service=self.sushibars[0] if self.sushibars else None,
                )
            except Exception as e:  # noqa: BLE001
                logger.opt(exception=True).error(
                    f"FarmService: Fehler bei Fahrzeuglogistik: {e}"
                )

        logger.info("========== FarmService: Zyklus beendet ==========")

