from app.modules.farm_buildings.shed import Shed
from app.modules.seasonpass.handlers.base import BaseTaskHandler
from app.modules.seasonpass.task_registry import register_task


@register_task("startproduction")
class StartProductionTaskHandler(BaseTaskHandler):
    """Handles seasonpass task 'startproduction': feed animal shed to start production."""

    task_type = "startproduction"

    async def run(self) -> bool:
        target_b_id = self.task.building
        self.logger.info(
            f"Seasonpass: Starte StartProduction-Task {self.task.id} (Gebäude {target_b_id}, PID {self.task.pid})..."
        )

        matched_shed = None
        if self.farm_service and hasattr(self.farm_service, "sheds"):
            for shed in self.farm_service.sheds:
                if target_b_id is None or shed.building_id == target_b_id:
                    matched_shed = shed
                    break

        if not matched_shed:
            # Fallback: chicken coop on farm 1 position 2
            matched_shed = Shed(self.client, farm_id=1, position=2, building_id=target_b_id or 2)

        await matched_shed.update()
        if matched_shed.barn and matched_shed.barn.is_ready:
            await matched_shed.crop()
            await matched_shed.update()

        if self.stock_service:
            fed = await matched_shed.feed(self.stock_service)
            if fed:
                self.logger.info(f"Seasonpass: Stall {matched_shed.farm_id}/{matched_shed.position} erfolgreich gefüttert.")
                return True

        return False


@register_task("harvestproduction")
class HarvestProductionTaskHandler(BaseTaskHandler):
    """Handles seasonpass task 'harvestproduction': harvest completed shed products."""

    task_type = "harvestproduction"

    async def run(self) -> bool:
        target_b_id = self.task.building
        self.logger.info(
            f"Seasonpass: Starte HarvestProduction-Task {self.task.id} (Gebäude {target_b_id})..."
        )

        if self.farm_service and hasattr(self.farm_service, "sheds"):
            for shed in self.farm_service.sheds:
                if target_b_id is None or shed.building_id == target_b_id:
                    await shed.update()
                    if shed.barn and shed.barn.is_ready:
                        await shed.crop()
                        self.logger.info(f"Seasonpass: Stall {shed.farm_id}/{shed.position} erfolgreich abgeerntet.")
                        return True

        return True
