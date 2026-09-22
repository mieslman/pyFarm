from app.models.product import Product
from app.modules.agriculture.field import Field
from app.modules.seasonpass.handlers.base import BaseTaskHandler
from app.modules.seasonpass.task_registry import register_task


@register_task("plant")
class PlantTaskHandler(BaseTaskHandler):
    """Handles seasonpass task 'plant': sow requested crop on preferred field."""

    task_type = "plant"

    async def run(self) -> bool:
        farm_id = self.config.preferred_field_farm
        pos = self.config.preferred_field_pos
        pid = self.task.pid

        self.logger.info(
            f"Seasonpass: Starte Plant-Task {self.task.id} (Pflanze PID {pid}, Bedarf: {self.task.count}) "
            f"auf Feld {farm_id}/{pos}..."
        )

        field = None
        if self.farm_service and hasattr(self.farm_service, "fields"):
            for f in self.farm_service.fields:
                if f.farm_id == farm_id and f.position == pos:
                    field = f
                    break
        if not field:
            field = Field(self.client, farm_id=farm_id, position=pos)

        # Update field data
        await field.update()

        # If field has ripe crops, crop them first
        if field.has_ready_crops:
            await field.crop()
            await field.update()

        if pid:
            prod_name = f"PID {pid}"
            if self.stock_service:
                p = self.stock_service.get_product(pid)
                if p:
                    prod_name = p.name

            plant_obj = Product(pid=pid, name=prod_name, category="v")
            planted = await field.plant(plant_obj)
            if planted:
                await field.water()
                self.logger.info(f"Seasonpass: Pflanze '{prod_name}' erfolgreich gesät und gegossen.")
                return True
            else:
                # Direct autoplant fallback if tiles were already partially filled
                try:
                    await self.client.api_call(
                        "farm",
                        {"mode": "autoplant", "farm": farm_id, "position": pos, "id": pid, "product": pid},
                    )
                    await field.water()
                    return True
                except Exception as e:
                    self.logger.warning(f"Seasonpass: Fehler beim Säen von PID {pid}: {e}")
                    return False
        return False


@register_task("harvest")
class HarvestTaskHandler(BaseTaskHandler):
    """Handles seasonpass task 'harvest': crop fields."""

    task_type = "harvest"

    async def run(self) -> bool:
        farm_id = self.config.preferred_field_farm
        pos = self.config.preferred_field_pos

        self.logger.info(
            f"Seasonpass: Starte Harvest-Task {self.task.id} (Ernten auf Feld {farm_id}/{pos})..."
        )

        field = None
        if self.farm_service and hasattr(self.farm_service, "fields"):
            for f in self.farm_service.fields:
                if f.farm_id == farm_id and f.position == pos:
                    field = f
                    break
        if not field:
            field = Field(self.client, farm_id=farm_id, position=pos)

        await field.update()
        if field.has_ready_crops:
            await field.crop()
            self.logger.info(f"Seasonpass: Ernte auf Feld {farm_id}/{pos} durchgeführt.")
            return True
        else:
            self.logger.debug(f"Seasonpass: Keine reifen Pflanzen auf Feld {farm_id}/{pos} zum Ernten.")
            return True


@register_task("water")
class WaterTaskHandler(BaseTaskHandler):
    """Handles seasonpass task 'water': water field tiles."""

    task_type = "water"

    async def run(self) -> bool:
        farm_id = self.config.preferred_field_farm
        pos = self.config.preferred_field_pos

        self.logger.info(
            f"Seasonpass: Starte Water-Task {self.task.id} (Gießen auf Feld {farm_id}/{pos})..."
        )

        field = None
        if self.farm_service and hasattr(self.farm_service, "fields"):
            for f in self.farm_service.fields:
                if f.farm_id == farm_id and f.position == pos:
                    field = f
                    break
        if not field:
            field = Field(self.client, farm_id=farm_id, position=pos)

        await field.update()
        await field.water()
        self.logger.info(f"Seasonpass: Gießen auf Feld {farm_id}/{pos} durchgeführt.")
        return True
