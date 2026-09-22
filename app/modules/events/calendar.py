from typing import Any, Optional
from loguru import logger

from app.core.client import MFFGameClient
from app.modules.events.models import CalendarStatus


class CalendarEventService:
    """Manages the seasonal Advent/Easter calendar event (opening daily doors)."""

    def __init__(self, client: MFFGameClient):
        self.client = client
        self.last_status: Optional[CalendarStatus] = None

    async def get_status(self) -> Optional[CalendarStatus]:
        """Fetch and parse calendar state from server."""
        try:
            res = await self.client.api_call("farm", {"mode": "calendar_init"})
            datablock = res.get("datablock", {})
            if not isinstance(datablock, dict) or not datablock.get("data"):
                logger.debug("CalendarEventService: Kein aktives Kalender-Event vorhanden.")
                return None

            actual_day = int(datablock.get("day", 0))
            config_fields = datablock.get("config", {}).get("fields", {})
            opened_days = datablock.get("data", {}).get("days", {})

            opened_day_numbers: set[int] = set()
            if isinstance(opened_days, dict):
                opened_day_numbers = {int(k) for k in opened_days.keys() if str(k).isdigit()}
            elif isinstance(opened_days, list):
                opened_day_numbers = {int(x) for x in opened_days if str(x).isdigit()}

            all_days = {int(k) for k in config_fields.keys() if str(k).isdigit()}
            unopened = sorted(list(all_days - opened_day_numbers))
            is_opened_today = actual_day in opened_day_numbers

            status = CalendarStatus(
                day=actual_day,
                unopened_days=unopened,
                is_opened_today=is_opened_today,
            )
            self.last_status = status
            return status

        except Exception as e:
            logger.warning(f"CalendarEventService: Fehler beim Abruf von calendar_init: {e}")
            return None

    async def serve(self) -> bool:
        """Inspect calendar and open today's door if not already opened."""
        try:
            res = await self.client.api_call("farm", {"mode": "calendar_init"})
            datablock = res.get("datablock", {})
            if not isinstance(datablock, dict) or not datablock.get("data"):
                logger.debug("CalendarEventService: Keine Kalender-Daten gefunden.")
                return False

            actual_day = datablock.get("day")
            if not actual_day:
                return False

            fields = datablock.get("config", {}).get("fields", {})
            days = datablock.get("data", {}).get("days", {})

            # Check if actual day is not opened and available in config fields
            day_str = str(actual_day)
            is_opened = day_str in days if isinstance(days, dict) else actual_day in days
            is_valid_field = day_str in fields

            if not is_opened and is_valid_field:
                logger.info(f"CalendarEventService: Öffne Kalendertürchen für Tag {actual_day}...")
                open_res = await self.client.api_call(
                    "farm",
                    {"mode": "calendar_openfield", "field": actual_day, "day": 1},
                )
                open_datablock = open_res.get("datablock", {})
                if open_datablock and open_datablock.get("day"):
                    logger.info(f"CalendarEventService: Türchen {actual_day} erfolgreich geöffnet.")
                    await self.get_status()
                    return True
                else:
                    logger.warning(f"CalendarEventService: Konnte Türchen {actual_day} nicht öffnen: {open_res}")
                    return False
            else:
                logger.debug(f"CalendarEventService: Türchen {actual_day} bereits geöffnet oder ungültig.")
                return False

        except Exception as e:
            logger.error(f"CalendarEventService: Unerwarteter Fehler im Kalender-Zyklus: {e}")
            return False
