"""FarmersMarketService: Master orchestrator for all Dorf 2 market domains."""

from loguru import logger

from app.config import FarmersMarketConfig
from app.core.client import MFFGameClient
from app.modules.farmersmarket.farmis import MarketFarmisService
from app.modules.farmersmarket.flower_area import FlowerAreaService
from app.modules.farmersmarket.flower_slots import FlowerSlotsService
from app.modules.farmersmarket.models import FarmersMarketSummary
from app.modules.farmersmarket.nursery import NurseryService
from app.modules.farmersmarket.order_manager import FlowerOrderManager
from app.modules.farmersmarket.petbreed import PetBreedService
from app.services.stock_service import StockService


class FarmersMarketService:
    """Coordinates Nursery, FlowerArea, FlowerSlots, Farmis, and PetBreed in Dorf 2."""

    def __init__(
        self,
        client: MFFGameClient,
        stock_service: StockService | None = None,
        config: FarmersMarketConfig | None = None,
    ) -> None:
        self.client = client
        self.stock_service = stock_service
        self.config = config or FarmersMarketConfig()

        self.order_manager = FlowerOrderManager()
        self.nursery = NurseryService(client, stock_service)
        self.flower_area = FlowerAreaService(client, stock_service)
        self.flower_slots = FlowerSlotsService(client, stock_service)
        self.farmis = MarketFarmisService(client, stock_service)
        self.pet_breed = PetBreedService(
            client,
            stock_service,
            enabled=self.config.pet_breed_enabled,
            daily_parts_enabled=self.config.pet_daily_parts,
        )

    async def run_cycle(self) -> dict:
        """Execute a full farmers market cycle."""
        if not self.config.enabled:
            logger.debug("FarmersMarketService: Modul ist deaktiviert.")
            return {"enabled": False}

        logger.info("=== Starte Bauernmarkt-Zyklus (Dorf 2) ===")
        results: dict = {
            "farmis_served": 0,
            "nursery_harvested": 0,
            "nursery_produced": 0,
            "flowers_harvested": 0,
            "flowers_watered": False,
            "flowers_planted": 0,
            "slots_removed": 0,
            "slots_watered": 0,
            "slots_planted": False,
            "pet_breed_harvested": 0,
        }

        # 1. Fetch current status
        try:
            res = await self.client.api_call("farm", {"mode": "getfarms", "farm": 1, "position": 0})
            fm_data = res.get("updateblock", {}).get("farmersmarket", {})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"FarmersMarketService: Fehler beim Laden der Marktdaten: {e}")
            return results

        if not fm_data:
            logger.warning("FarmersMarketService: Keine 'farmersmarket' Daten in Upstream-Antwort.")
            return results

        # 2. Update sub-service states
        self.nursery.update(fm_data)
        self.flower_area.update(fm_data)
        self.flower_slots.update(fm_data)
        self.farmis.update(fm_data)
        self.pet_breed.update(fm_data)

        # Reset order manager for current cycle's customer demands
        self.order_manager.clear()

        # 3. Market Farmis: evaluate customer demand
        if self.config.farmis_enabled:
            try:
                served = await self.farmis.serve_and_collect_orders(self.order_manager)
                results["farmis_served"] = served
            except Exception as e:  # noqa: BLE001
                logger.warning(f"FarmersMarketService: Fehler bei Farmis: {e}")

        # 4. Gärtnerei (Nursery): harvest ready and craft demanded arrangements
        if self.config.nursery_enabled:
            try:
                harvested = await self.nursery.harvest()
                produced = await self.nursery.produce(self.order_manager)
                results["nursery_harvested"] = harvested
                results["nursery_produced"] = produced
            except Exception as e:  # noqa: BLE001
                logger.warning(f"FarmersMarketService: Fehler in Gärtnerei: {e}")

        # 5. Blumenwiese (FlowerArea): harvest, plant empty beds, then water all
        if self.config.flower_area_enabled:
            try:
                f_harvested = await self.flower_area.harvest()
                f_planted = await self.flower_area.plant(max_batch=self.config.max_flower_batch)
                f_watered = await self.flower_area.water()
                results["flowers_harvested"] = f_harvested
                results["flowers_planted"] = f_planted
                results["flowers_watered"] = f_watered
            except Exception as e:  # noqa: BLE001
                logger.warning(f"FarmersMarketService: Fehler auf Blumenwiese: {e}")

        # 6. Schau-Slots (FlowerSlots): maintain display
        if self.config.flower_slots_enabled:
            try:
                s_removed = await self.flower_slots.remove_expired()
                s_watered = await self.flower_slots.water()
                s_planted = await self.flower_slots.plant_arrangement()
                results["slots_removed"] = s_removed
                results["slots_watered"] = s_watered
                results["slots_planted"] = s_planted
            except Exception as e:  # noqa: BLE001
                logger.warning(f"FarmersMarketService: Fehler bei Schau-Slots: {e}")

        # 7. Tierzucht (PetBreed): strictly checks config flag
        if self.config.pet_breed_enabled:
            try:
                pb_harvested = await self.pet_breed.run_cycle()
                results["pet_breed_harvested"] = pb_harvested
            except Exception as e:  # noqa: BLE001
                logger.warning(f"FarmersMarketService: Fehler in Tierzucht: {e}")
        else:
            logger.debug("Tierzucht (PetBreed) ist laut Konfiguration inaktiv - wird übersprungen.")

        logger.info(
            f"=== Bauernmarkt-Zyklus abgeschlossen: "
            f"Kunden bedient: {results['farmis_served']}, "
            f"Gestecke geerntet/produziert: {results['nursery_harvested']}/{results['nursery_produced']}, "
            f"Blumen geerntet/gepflanzt: {results['flowers_harvested']}/{results['flowers_planted']} ==="
        )
        return results

    def get_summary(self) -> FarmersMarketSummary:
        """Return a structured live summary for REST API and Web Dashboard."""
        nursery_ready = sum(1 for s in self.nursery.state.slots.values() if s.is_ready)
        nursery_active = sum(1 for s in self.nursery.state.slots.values() if not s.is_free and not s.is_blocked)

        fields = list(self.flower_area.state.fields.values())
        f_ready = sum(1 for f in fields if f.is_ready)
        f_empty = sum(1 for f in fields if f.is_empty)
        f_growing = len(fields) - f_ready - f_empty

        active_slots = sum(1 for s in self.flower_slots.state.slots.values() if not s.is_empty)
        waiting_farmis = sum(1 for f in self.farmis.farmis if f.status == 0)

        pb_status = "Aktiv" if self.config.pet_breed_enabled else "Inaktiv (Konfiguration)"

        return FarmersMarketSummary(
            enabled=self.config.enabled,
            nursery_ready_count=nursery_ready,
            nursery_active_count=nursery_active,
            flower_fields_total=len(fields) if fields else 36,
            flower_fields_ready=f_ready,
            flower_fields_growing=f_growing,
            flower_fields_empty=f_empty,
            flower_slots_active=active_slots,
            farmis_waiting=waiting_farmis,
            farmis_served_total=self.farmis.total_served,
            pet_breed_enabled=self.config.pet_breed_enabled,
            pet_breed_status=pb_status,
            details={
                "nursery_slots": [s.model_dump() for s in self.nursery.state.slots.values()],
                "flower_fields": [f.model_dump() for f in fields],
                "display_slots": [s.model_dump() for s in self.flower_slots.state.slots.values()],
                "waiting_farmis": [f.model_dump() for f in self.farmis.farmis if f.status == 0],
            },
        )
