from app.modules.seasonpass.handlers.base import BaseTaskHandler
from app.modules.seasonpass.task_registry import register_task


@register_task("forestryplant")
class ForestryPlantTaskHandler(BaseTaskHandler):
    """Handles seasonpass task 'forestryplant': clear slot 1, plant requested tree and water."""

    task_type = "forestryplant"

    async def run(self) -> bool:
        pid = self.task.pid
        pos = self.config.preferred_forestry_pos

        self.logger.info(
            f"Seasonpass: Starte ForestryPlant-Task {self.task.id} (Baum PID {pid} auf Platz {pos})..."
        )

        try:
            # 1. Clear slot pos
            await self.client.api_call("forestry", {"action": "cancelcrop", "position": pos})
            # 2. Plant tree
            if pid:
                await self.client.api_call(
                    "forestry",
                    {"action": "plant", "z[]": pos, "p[]": pid},
                )
            # 3. Water forestry
            await self.client.api_call("forestry", {"action": "water"})
            self.logger.info(f"Seasonpass: Baum PID {pid} auf Platz {pos} gepflanzt und bewässert.")
            return True
        except Exception as e:
            self.logger.warning(f"Seasonpass: Fehler bei ForestryPlant-Task: {e}")
            return False


@register_task("forestryharvest")
class ForestryHarvestTaskHandler(BaseTaskHandler):
    """Handles seasonpass task 'forestryharvest': harvest ripe trees."""

    task_type = "forestryharvest"

    async def run(self) -> bool:
        self.logger.info(f"Seasonpass: Starte ForestryHarvest-Task {self.task.id}...")
        try:
            await self.client.api_call("forestry", {"action": "harvest"})
            self.logger.info("Seasonpass: Forestry-Ernte erfolgreich aufgerufen.")
            return True
        except Exception as e:
            self.logger.warning(f"Seasonpass: Fehler bei ForestryHarvest-Task: {e}")
            return False


@register_task("forestrywater")
class ForestryWaterTaskHandler(BaseTaskHandler):
    """Handles seasonpass task 'forestrywater': water trees."""

    task_type = "forestrywater"

    async def run(self) -> bool:
        self.logger.info(f"Seasonpass: Starte ForestryWater-Task {self.task.id}...")
        try:
            await self.client.api_call("forestry", {"action": "water"})
            self.logger.info("Seasonpass: Forestry-Bewässerung erfolgreich aufgerufen.")
            return True
        except Exception as e:
            self.logger.warning(f"Seasonpass: Fehler bei ForestryWater-Task: {e}")
            return False
