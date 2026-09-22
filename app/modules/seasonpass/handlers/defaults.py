from app.modules.seasonpass.handlers.base import BaseTaskHandler


class NoOpTaskHandler(BaseTaskHandler):
    """Fallback handler that logs the task without performing actions."""

    task_type = "noop"

    async def run(self) -> bool:
        self.logger.info(
            f"Seasonpass: NoOp-Handler für Task {self.task.id} (Typ '{self.task.type}'): "
            f"Keine Aktion erforderlich oder Typ nicht unterstützt. Payload: {self.task.payload}"
        )
        return True
