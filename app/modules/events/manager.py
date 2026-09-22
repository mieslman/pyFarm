import re
from datetime import datetime
from typing import Any, Optional
from loguru import logger

from app.config import settings
from app.core.client import MFFGameClient
from app.modules.events.calendar import CalendarEventService
from app.modules.events.delivery import DeliveryEventService
from app.modules.events.eventgarden import EventGardenService
from app.modules.events.models import EventsOverview
from app.modules.events.oktoberfest import OktoberfestEventService
from app.modules.events.olympia import OlympiaEventService
from app.modules.events.pentecost import PentecostEventService


class EventManager:
    """Central orchestrator for all seasonal and temporary events in MyFreeFarm."""

    def __init__(self, client: MFFGameClient):
        self.client = client
        self.calendar = CalendarEventService(client)
        self.delivery = DeliveryEventService(client)
        self.eventgarden = EventGardenService(client)
        self.oktoberfest = OktoberfestEventService(client)
        self.pentecost = PentecostEventService(client)
        self.olympia = OlympiaEventService(client)

        self.last_detected_events: list[str] = []
        self.last_run_timestamp: Optional[str] = None

    async def detect_active_events(self, html_content: Optional[str] = None) -> list[str]:
        """Detect active seasonal events from the JavaScript configuration on main.php."""
        if html_content is None:
            if not self.client.rid:
                logger.debug("EventManager: Kein Login vorhanden, überspringe Event-Erkennung.")
                return []
            try:
                url = f"https://s{self.client.server}.myfreefarm.de/main.php"
                res = await self.client.client.get(url)
                html_content = res.text
            except Exception as e:
                logger.warning(f"EventManager: Fehler beim Laden von main.php für Event-Erkennung: {e}")
                return self.last_detected_events

        # Regex to find events.data = [...]
        events_data_regex = re.compile(r"events\.data\s*=\s*\[(.*?)\];", re.DOTALL)
        match = events_data_regex.search(html_content)
        if not match:
            logger.debug("EventManager: Kein 'events.data' in main.php gefunden.")
            self.last_detected_events = []
            return []

        events_str = match.group(1)
        name_regex = re.compile(r'["\']name["\']\s*:\s*["\']([a-zA-Z0-9_-]+)["\']')
        detected_names = name_regex.findall(events_str)

        logger.info(f"EventManager: Erkannte aktive Events auf Server {self.client.server}: {detected_names}")
        self.last_detected_events = detected_names
        return detected_names

    def is_event_enabled(self, event_name: str) -> bool:
        """Check if an event is enabled in configuration."""
        cfg = settings.events
        if not cfg.enabled:
            return False

        mapping = {
            "calendar": cfg.calendar_enabled,
            "deliveryevent": cfg.delivery_enabled,
            "eventgarden": cfg.eventgarden_enabled,
            "oktoberfest": cfg.oktoberfest_enabled,
            "pentecostevent": cfg.pentecost_enabled,
            "pentecost": cfg.pentecost_enabled,
            "olympia": cfg.olympia_enabled,
        }
        return mapping.get(event_name.lower(), True)

    async def serve(self, html_content: Optional[str] = None) -> dict[str, Any]:
        """Execute a service cycle for all currently active seasonal events."""
        results: dict[str, Any] = {}
        self.last_run_timestamp = datetime.now().isoformat()

        if not settings.events.enabled:
            logger.debug("EventManager: Saisonevents sind global deaktiviert.")
            return results

        active_events = await self.detect_active_events(html_content=html_content)

        # If no events auto-detected, but user configured specific events without auto_detect
        if not active_events and not settings.events.auto_detect:
            possible_events = ["calendar", "deliveryevent", "eventgarden", "oktoberfest", "pentecostevent", "olympia"]
            active_events = [e for e in possible_events if self.is_event_enabled(e)]
            logger.debug(f"EventManager: Verwende manuell aktivierte Events: {active_events}")

        logger.info(f"========== EventManager: Starte Zyklus für {len(active_events)} aktive(s) Event(s) ==========")

        for event in active_events:
            event_clean = event.lower()
            if not self.is_event_enabled(event_clean):
                logger.info(f"EventManager: Event '{event}' ist laut Konfiguration deaktiviert.")
                continue

            logger.info(f"EventManager: Bearbeite Event '{event}'...")
            try:
                if event_clean == "calendar":
                    res = await self.calendar.serve()
                    results["calendar"] = res
                elif event_clean == "deliveryevent":
                    res = await self.delivery.serve()
                    results["deliveryevent"] = res
                elif event_clean == "eventgarden":
                    res = await self.eventgarden.serve()
                    results["eventgarden"] = res
                elif event_clean == "oktoberfest":
                    res = await self.oktoberfest.serve()
                    results["oktoberfest"] = res
                elif event_clean in ("pentecostevent", "pentecost"):
                    res = await self.pentecost.serve()
                    results["pentecost"] = res
                elif event_clean == "olympia":
                    res = await self.olympia.serve()
                    results["olympia"] = res
                else:
                    logger.warning(f"EventManager: Kein Handler für Event '{event}' registriert.")
            except Exception as e:
                logger.error(f"EventManager: Fehler beim Bearbeiten von Event '{event}': {e}")
                results[event] = False

        logger.info("========== EventManager: Zyklus abgeschlossen ==========")
        return results

    async def get_overview(self) -> EventsOverview:
        """Return a consolidated status overview across all event services."""
        return EventsOverview(
            active_events=self.last_detected_events,
            calendar=self.calendar.last_status,
            delivery=self.delivery.last_status,
            eventgarden=self.eventgarden.last_status,
            oktoberfest=self.oktoberfest.last_status,
            pentecost=self.pentecost.last_status,
            olympia=self.olympia.last_status,
            last_run=self.last_run_timestamp,
        )
