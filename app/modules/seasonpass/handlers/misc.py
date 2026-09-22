from typing import Any
from app.modules.helpers.helpers_service import HelpersService
from app.modules.seasonpass.handlers.base import BaseTaskHandler
from app.modules.seasonpass.task_registry import register_task


@register_task("weather")
class WeatherTaskHandler(BaseTaskHandler):
    """Handles seasonpass task 'weather': query the weather station in town."""

    task_type = "weather"

    async def run(self) -> bool:
        self.logger.info(f"Seasonpass: Starte Weather-Task {self.task.id} (Wetterstation aufrufen)...")
        try:
            await self.client.api_call("farm", {"mode": "weather_init", "farm": 2, "position": 0})
            self.logger.info("Seasonpass: Wetterstation erfolgreich aufgerufen.")
            return True
        except Exception as e:
            self.logger.warning(f"Seasonpass: Fehler beim Aufruf der Wetterstation: {e}")
            return False


@register_task("friendvisit")
class FriendVisitTaskHandler(BaseTaskHandler):
    """Handles seasonpass task 'friendvisit': visit a friend's showcase garden via friends list."""

    task_type = "friendvisit"

    async def run(self) -> bool:
        self.logger.info(f"Seasonpass: Starte FriendVisit-Task {self.task.id} (Freundesliste abrufen)...")
        friend_unr: str | None = None

        # 1. Fetch friends list from game server
        try:
            friends_data = await self.client.api_call("farm", {"mode": "friends_init"})
            datablock = friends_data.get("datablock", {})
            raw_data = datablock.get("data", {}) if isinstance(datablock, dict) else {}

            raw_friends: list[Any] = []
            if isinstance(raw_data, dict) and "friends" in raw_data:
                rf = raw_data["friends"]
                raw_friends = rf if isinstance(rf, list) else list(rf.values())
            elif isinstance(datablock, dict) and "friends" in datablock:
                rf = datablock["friends"]
                raw_friends = rf if isinstance(rf, list) else list(rf.values())

            for f in raw_friends:
                if isinstance(f, dict):
                    # In live Upjers data, 'friend' is the friend's UNR, while 'unr' is the player's own UNR
                    candidate = f.get("friend") or f.get("unr") or f.get("id")
                    if candidate:
                        friend_unr = str(candidate)
                        break
                elif str(f).isdigit():
                    friend_unr = str(f)
                    break
        except Exception as e:
            self.logger.warning(f"Seasonpass: Konnte Freundesliste nicht abrufen: {e}")

        # Fallback to configured friend_unr if friends list is empty
        if not friend_unr and getattr(self.config, "friend_unr", ""):
            friend_unr = str(self.config.friend_unr)

        if not friend_unr:
            self.logger.warning("Seasonpass: Kein Freund in der Freundesliste gefunden.")
            return False

        # 2. Visit friend's showcase garden
        try:
            self.logger.info(f"Seasonpass: Besuche Schaugarten von Freund (UNR {friend_unr})...")
            await self.client.api_call("farm", {"mode": "visitor_init", "unr": friend_unr})
            await self.client.api_call("farm", {"mode": "friends_init"})
            self.logger.info(f"Seasonpass: Schaugarten von {friend_unr} erfolgreich besucht.")
            return True
        except Exception as e:
            self.logger.warning(f"Seasonpass: Fehler beim Besuch des Schaugartens: {e}")
            return False


@register_task("farmi")
class FarmiTaskHandler(BaseTaskHandler):
    """Handles seasonpass task 'farmi': serve a satisfied customer at the farm."""

    task_type = "farmi"

    async def run(self) -> bool:
        self.logger.info(f"Seasonpass: Starte Farmi-Task {self.task.id}...")
        if not self.stock_service:
            self.logger.warning("Seasonpass: StockService nicht verfügbar für Farmi-Bedienung.")
            return False

        try:
            res = await self.client.api_call("farm", {"mode": "getfarms", "farm": 1, "position": 0})
            updateblock = res.get("updateblock", {})
            farmis_block = updateblock.get("farmis", [])
            offers = farmis_block[0] if isinstance(farmis_block, list) and len(farmis_block) > 0 and isinstance(farmis_block[0], list) else []

            for offer in offers:
                if not isinstance(offer, dict):
                    continue
                offer_id = str(offer.get("id", ""))
                is_sold = str(offer.get("verkauft", "0")) == "1"
                if is_sold:
                    continue

                # Check requested products (p1..p7 and a1..a7)
                can_fulfill = True
                for i in range(1, 8):
                    p_key = f"p{i}"
                    a_key = f"a{i}"
                    if p_key in offer and a_key in offer:
                        pid = int(offer[p_key]) if str(offer[p_key]).isdigit() else 0
                        amt = int(offer[a_key]) if str(offer[a_key]).isdigit() else 0
                        if pid > 0 and amt > 0:
                            if self.stock_service.get_amount(pid) < amt:
                                can_fulfill = False
                                break

                if can_fulfill:
                    self.logger.info(f"Seasonpass: Bediente Farmi {offer_id} (Preis: {offer.get('price')} kT)...")
                    await self.client.api_call(
                        "farm",
                        {
                            "mode": "sellfarmi",
                            "farm": 1,
                            "position": 1,
                            "id": offer_id,
                            "farmi": offer_id,
                            "status": 1,
                        },
                    )
                    self.logger.info(f"Seasonpass: Farmi {offer_id} erfolgreich bedient.")
                    return True

            self.logger.debug("Seasonpass: Kein Farmi-Auftrag mit aktuellem Lagerbestand erfüllbar.")
            return True
        except Exception as e:
            self.logger.warning(f"Seasonpass: Fehler bei Farmi-Task: {e}")
            return False


@register_task("startwindmillproduction")
class StartWindmillProductionTaskHandler(BaseTaskHandler):
    """Handles seasonpass task 'startwindmillproduction': start recipe in the windmill."""

    task_type = "startwindmillproduction"

    async def run(self) -> bool:
        self.logger.info(f"Seasonpass: Starte StartWindmillProduction-Task {self.task.id}...")
        if not self.stock_service:
            return False

        helpers = HelpersService(self.client, self.stock_service)
        try:
            started = await helpers.handle_windmill()
            self.logger.info(f"Seasonpass: Windmühle bedient (Erfolg: {started}).")
            return True
        except Exception as e:
            self.logger.warning(f"Seasonpass: Fehler bei Windmühlen-Task: {e}")
            return False
