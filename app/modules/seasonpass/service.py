from typing import Any
from loguru import logger

from app.core.client import MFFGameClient
# Trigger handler registrations
import app.modules.seasonpass.handlers  # noqa: F401
from app.modules.seasonpass.handlers.defaults import NoOpTaskHandler
from app.modules.seasonpass.models import SeasonPassConfig, SeasonPassSnapshot, SeasonPassTask
from app.modules.seasonpass.task_registry import get_task_handler
from app.services.stock_service import StockService


class SeasonPassService:
    """Coordinates fetching, executing and claiming rewards for the Seasonpass (Saisonale Reise)."""

    def __init__(
        self,
        client: MFFGameClient,
        config: SeasonPassConfig | None = None,
    ):
        self.client = client
        self.config = config or SeasonPassConfig()
        self.snapshot: SeasonPassSnapshot | None = None

    async def init_remote(self) -> SeasonPassSnapshot | None:
        """Call farm.php?mode=seasonpass_init to fetch remote season pass status."""
        try:
            res = await self.client.api_call("farm", {"mode": "seasonpass_init"})
            if res and isinstance(res, dict):
                self.snapshot = SeasonPassSnapshot.from_api(res)
                logger.debug(
                    f"Seasonpass: Snapshot geladen: Saison '{self.snapshot.season_name}', "
                    f"{self.snapshot.points} Punkte, {len(self.snapshot.pending_tasks)} offene Tasks."
                )
                return self.snapshot
        except Exception as e:
            logger.warning(f"Seasonpass: Fehler beim Abruf von seasonpass_init: {e}")
        return None

    async def claim_available_rewards(self) -> int:
        """Claim uncollected free tier rewards when point thresholds are met."""
        if not self.config.auto_claim_rewards or not self.snapshot:
            return 0

        claimed_count = 0
        for lvl_num, lvl in sorted(self.snapshot.levels.items()):
            if self.snapshot.points >= lvl.points and not lvl.is_claimed and lvl.free_rewards:
                logger.info(f"Seasonpass: Hole freie Belohnung für Stufe {lvl_num} ab ({lvl.points} Punkte)...")
                try:
                    res = await self.client.api_call(
                        "farm",
                        {"mode": "seasonpass_getreward", "level": lvl_num, "type": "free"},
                    )
                    datablock = res.get("datablock")
                    success = datablock == 1 or datablock == [1] or (isinstance(datablock, list) and 1 in datablock) or (isinstance(datablock, dict) and "data" in datablock)
                    if success or res.get("status") == "ok":
                        lvl.is_claimed = True
                        claimed_count += 1
                        logger.info(f"Seasonpass: Stufe {lvl_num} erfolgreich abgeholt.")
                except Exception as e:
                    logger.warning(f"Seasonpass: Fehler beim Abholen von Stufe {lvl_num}: {e}")

        return claimed_count

    async def serve(
        self,
        stock_service: StockService | None = None,
        farm_service: Any | None = None,
    ) -> dict[str, Any]:
        """Execute one complete seasonpass serving cycle."""
        if not self.config.enabled:
            logger.debug("Seasonpass: Deaktiviert in Konfiguration.")
            return {"enabled": False}

        logger.info("========== Seasonpass: Starte Aufgabenzyklus ==========")
        await self.init_remote()

        results: dict[str, Any] = {
            "enabled": True,
            "tasks_executed": 0,
            "rewards_claimed": 0,
            "pending_count": 0,
            "season_name": "",
            "points": 0,
        }

        if not self.snapshot:
            logger.warning("Seasonpass: Konnte keinen Snapshot laden.")
            return results

        results["season_name"] = self.snapshot.season_name
        results["points"] = self.snapshot.points

        pending_tasks = [t for t in self.snapshot.pending_tasks if not t.is_completed]
        results["pending_count"] = len(pending_tasks)

        logger.info(
            f"Seasonpass: '{self.snapshot.season_name}' mit {self.snapshot.points} Punkten. "
            f"{len(pending_tasks)} unerledigte Aufgabe(n) gefunden."
        )

        for task in pending_tasks:
            handler_cls = get_task_handler(task.type) or NoOpTaskHandler
            handler = handler_cls(
                task=task,
                client=self.client,
                stock_service=stock_service,
                farm_service=farm_service,
                config=self.config,
            )

            try:
                success = await handler.run()
                if success:
                    results["tasks_executed"] += 1
            except Exception as e:
                logger.opt(exception=True).error(
                    f"Seasonpass: Fehler bei Task {task.id} (Typ {task.type}): {e}"
                )

        # Claim newly unlocked rewards
        if self.config.auto_claim_rewards:
            rewards_claimed = await self.claim_available_rewards()
            results["rewards_claimed"] = rewards_claimed

        logger.info("========== Seasonpass: Zyklus abgeschlossen ==========")
        return results

    def get_summary(self) -> dict[str, Any]:
        """Return structured summary for REST API and dashboard."""
        if not self.snapshot:
            return {
                "active": False,
                "season_name": "",
                "points": 0,
                "remain": 0,
                "pending_tasks": [],
                "completed_tasks": [],
                "levels": {},
            }

        return {
            "active": True,
            "season_id": self.snapshot.season_id,
            "season_name": self.snapshot.season_name,
            "points": self.snapshot.points,
            "remain": self.snapshot.remain,
            "pending_tasks": [t.model_dump() for t in self.snapshot.pending_tasks],
            "completed_tasks": [t.model_dump() for t in self.snapshot.completed_tasks],
            "levels": {k: v.model_dump() for k, v in self.snapshot.levels.items()},
        }
