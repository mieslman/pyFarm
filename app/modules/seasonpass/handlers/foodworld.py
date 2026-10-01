from app.modules.foodworld.kitchen import KitchenService
from app.modules.seasonpass.handlers.base import BaseTaskHandler
from app.modules.seasonpass.task_registry import register_task


@register_task("foodworldstartproduction")
class FoodworldStartProductionTaskHandler(BaseTaskHandler):
    """Handles seasonpass task 'foodworldstartproduction': cook dishes in Foodworld kitchen."""

    task_type = "foodworldstartproduction"

    async def run(self) -> bool:
        pid = self.task.pid
        self.logger.info(
            f"Seasonpass: Starte FoodworldStartProduction-Task {self.task.id} (Gericht PID {pid})..."
        )

        try:
            res = await self.client.api_call(
                "foodworld",
                {"action": "foodworld_init", "id": 0, "table": 0, "chair": 0},
            )
            db = res.get("datablock", {})
            if isinstance(db, dict):
                kitchen = KitchenService(self.client, self.stock_service)
                kitchen.update(db)
                await kitchen.pickup_products()

                # Produce requested dish if specified (force_produce=True ignores stock for production tasks)
                demanded = {pid: self.task.count} if pid else {}
                cooked = await kitchen.produce(
                    demanded_cart=demanded,
                    force_produce=True,
                    auto_buy_ingredients=True,
                )
                self.logger.info(
                    f"Seasonpass: Foodworld-Küche gestartet ({cooked} Gerichte gekocht)."
                )
                return True
        except Exception as e:
            self.logger.warning(f"Seasonpass: Fehler bei FoodworldStartProduction-Task: {e}")

        return False


@register_task("foodworldharvestproduction")
class FoodworldHarvestProductionTaskHandler(BaseTaskHandler):
    """Handles seasonpass task 'foodworldharvestproduction': collect finished dishes."""

    task_type = "foodworldharvestproduction"

    async def run(self) -> bool:
        pid = self.task.pid
        self.logger.info(
            f"Seasonpass: Starte FoodworldHarvestProduction-Task {self.task.id} (Gericht PID {pid})..."
        )

        try:
            res = await self.client.api_call(
                "foodworld",
                {"action": "foodworld_init", "id": 0, "table": 0, "chair": 0},
            )
            db = res.get("datablock", {})
            if isinstance(db, dict):
                kitchen = KitchenService(self.client, self.stock_service)
                kitchen.update(db)
                picked = await kitchen.pickup_products()
                self.logger.info(
                    f"Seasonpass: Foodworld-Küche geleert ({picked} Gerichte abgeholt)."
                )

                # If a specific dish is demanded for harvest, check if it's currently cooking.
                # If not currently cooking and wasn't just ready/picked up, start production!
                if pid:
                    is_cooking = False
                    for b in kitchen.buildings.values():
                        for s in b.slots.values():
                            if s.pid == pid and not s.is_free:
                                is_cooking = True
                                break

                    if is_cooking:
                        self.logger.info(
                            f"Seasonpass: Gericht PID {pid} wird aktuell zubereitet (Warten auf Fertigstellung)."
                        )
                    else:
                        self.logger.info(
                            f"Seasonpass: Gericht PID {pid} kocht noch nicht. Starte Vorab-Zubereitung für Harvest-Task..."
                        )
                        cooked = await kitchen.produce(
                            demanded_cart={pid: self.task.count},
                            force_produce=True,
                            auto_buy_ingredients=True,
                        )
                        self.logger.info(
                            f"Seasonpass: Vorab-Zubereitung gestartet ({cooked} Gerichte gekocht)."
                        )

                return True
        except Exception as e:
            self.logger.warning(f"Seasonpass: Fehler bei FoodworldHarvestProduction-Task: {e}")

        return False
