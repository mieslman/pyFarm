from typing import Any, Optional
from loguru import logger

from app.core.client import MFFGameClient
from app.modules.events.models import OlympiaStatus


class OlympiaEventService:
    """Manages the Olympia event: exchanging event berries for competition energy."""

    def __init__(self, client: MFFGameClient):
        self.client = client
        self.last_status: Optional[OlympiaStatus] = None

    async def get_status(self) -> Optional[OlympiaStatus]:
        """Fetch and parse Olympia event status."""
        try:
            res = await self.client.api_call("main", {"action": "olympia_init"})
            datablock = res.get("datablock", {})
            if not isinstance(datablock, dict) or "data" not in datablock:
                return None

            data_block = datablock.get("data", {})
            energy = int(
                datablock.get("energy")
                or data_block.get("lastupdate", {}).get("energy")
                or data_block.get("energy")
                or 0
            )
            berries = int(data_block.get("berries", 0))

            needed_berries = (100 - energy) * 20 if energy < 100 else 0
            can_part = energy < 100 and berries >= needed_berries

            status = OlympiaStatus(
                energy=energy,
                berries=berries,
                can_participate=can_part,
            )
            self.last_status = status
            return status

        except Exception as e:
            logger.warning(f"OlympiaEventService: Fehler beim Abruf von olympia_init: {e}")
            return None

    async def serve(self) -> bool:
        """Evaluate Olympia berries/energy and participate in competition if eligible."""
        try:
            res = await self.client.api_call("main", {"action": "olympia_init"})
            datablock = res.get("datablock", {})
            if not isinstance(datablock, dict) or "data" not in datablock:
                logger.debug("OlympiaEventService: Kein aktives Olympia-Event vorhanden.")
                return False

            data_block = datablock.get("data", {})
            energy = int(
                datablock.get("energy")
                or data_block.get("lastupdate", {}).get("energy")
                or data_block.get("energy")
                or 0
            )
            berries = int(data_block.get("berries", 0))

            needed_berries = (100 - energy) * 20
            if energy < 100 and berries >= needed_berries:
                logger.info(
                    f"OlympiaEventService: Reiche Olympia-Wettkampf ein (Energie: {energy}/100, "
                    f"Beeren: {berries}, Benötigt: {needed_berries})..."
                )
                entry_res = await self.client.api_call(
                    "main",
                    {"action": "olympia_entry", "amount": 10},
                )
                if entry_res.get("datablock"):
                    logger.info("OlympiaEventService: Olympia-Teilnahme erfolgreich eingereicht.")
                    await self.get_status()
                    return True
                else:
                    logger.warning(f"OlympiaEventService: Fehler bei Olympia-Teilnahme: {entry_res}")
                    return False
            else:
                logger.debug(
                    f"OlympiaEventService: Keine Teilnahme (Energie: {energy}, Beeren: {berries}/{needed_berries})."
                )
                return False

        except Exception as e:
            logger.error(f"OlympiaEventService: Unerwarteter Fehler im Olympia-Zyklus: {e}")
            return False
