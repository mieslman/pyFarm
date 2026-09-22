import random
from typing import Any

from loguru import logger

from app.config import HelpersConfig, settings
from app.core.client import MFFGameClient
from app.core.exceptions import UpstreamAPIError
from app.services.stock_service import StockService


class WindmillFormula:
    """Represents a bakeable recipe in the Windmill."""

    def __init__(self, raw: list[Any] | dict[str, Any]):
        if isinstance(raw, list):
            self.id: int = int(raw[0])
            self.name: str = str(raw[2]) if len(raw) > 2 else f"Rezept {self.id}"
            self.amount: int = 1
            raw_reqs = raw[3] if len(raw) > 3 else []
        elif isinstance(raw, dict):
            # PHP json_encode serializes arrays with custom properties (e.g. 'amount') as objects with string keys "0", "1", "2", "3"
            raw_id = raw.get("id") or raw.get("0") or raw.get(0) or 0
            self.id = int(raw_id)
            self.name = str(
                raw.get("name")
                or raw.get("2")
                or raw.get(2)
                or f"Rezept {self.id}"
            )
            self.amount = int(raw.get("amount", 1))
            raw_reqs = raw.get("requirements") or raw.get("3") or raw.get(3) or []
        else:
            self.id = 0
            self.name = "Unbekannt"
            self.amount = 1
            raw_reqs = []

        self.requirements: list[dict[str, int]] = []
        if isinstance(raw_reqs, list):
            for req in raw_reqs:
                if isinstance(req, (list, tuple)) and len(req) >= 2:
                    self.requirements.append({"pid": int(req[0]), "amount": int(req[1])})
                elif isinstance(req, dict) and "pid" in req and "amount" in req:
                    self.requirements.append({"pid": int(req["pid"]), "amount": int(req["amount"])})


class HelpersService:
    """Automates daily bonuses, helpers, and minor facilities (Ben, Donkey, Lottery, Windmill)."""

    def __init__(
        self,
        client: MFFGameClient,
        stock_service: StockService,
        config: HelpersConfig | None = None,
    ):
        self.client = client
        self.stock_service = stock_service
        self.config = config or settings.helpers

    async def handle_farm_dog(self, menue_data: dict[str, Any] | None = None) -> bool:
        """Claim daily Braver Ben production speed bonus (10 min boost)."""
        if not self.config.farm_dog:
            return False

        if menue_data is None:
            try:
                res = await self.client.api_call(
                    "farm", {"mode": "getfarms", "farm": 1, "position": 0}
                )
                menue_data = res.get("updateblock", {}).get("menue", {})
            except UpstreamAPIError as e:
                logger.warning(f"Helpers: Konnte Menü-Status für Braver Ben nicht abrufen: {e}")
                return False

        # In MFF, farmdog_harvest truthy means already claimed today
        already_claimed = bool(menue_data.get("farmdog_harvest"))
        if already_claimed:
            logger.debug("Helpers: Braver Ben heute bereits gestreichelt.")
            return False

        logger.info("Helpers: Streichle Braven Ben für den täglichen 10-Minuten-Bonus...")
        try:
            res = await self.client.api_call("farm", {"mode": "dogbonus", "farm": 1, "position": 0})
            if res.get("datablock") == 1 or res.get("datablock", [None])[0] == 1:
                logger.info("Helpers: Braver Ben Bonus erfolgreich abgeholt.")
                return True
        except UpstreamAPIError as e:
            logger.warning(f"Helpers: Fehler bei Braver Ben: {e}")

        return False

    async def handle_donkey(self, menue_data: dict[str, Any] | None = None) -> bool:
        """Claim daily Goldesel Waltraud kT coin bonus."""
        if not self.config.donkey:
            return False

        if menue_data is None:
            try:
                res = await self.client.api_call(
                    "farm", {"mode": "getfarms", "farm": 1, "position": 0}
                )
                menue_data = res.get("updateblock", {}).get("menue", {})
            except UpstreamAPIError as e:
                logger.warning(f"Helpers: Konnte Menü-Status für Goldesel nicht abrufen: {e}")
                return False

        donkey_ready = int(menue_data.get("donkey", 0)) == 1
        if not donkey_ready:
            logger.debug("Helpers: Goldesel Waltraud heute nicht bereit.")
            return False

        logger.info("Helpers: Melke Goldesel Waltraud für tägliche kT-Münzen...")
        try:
            res = await self.client.api_call(
                "farm", {"mode": "dailydonkey", "farm": 1, "position": 1}
            )
            if res.get("datablock", [None])[0] == 1 or res.get("updateblock"):
                logger.info("Helpers: Goldesel Waltraud Bonus erfolgreich erhalten.")
                return True
        except UpstreamAPIError as e:
            logger.warning(f"Helpers: Fehler bei Goldesel: {e}")

        return False

    async def handle_lottery(self) -> bool:
        """Draw daily free lottery ticket at the lottery booth (Losbude, City 2)."""
        if not self.config.lottery:
            return False

        try:
            init_data = await self.client.api_call("city", {"mode": "initlottery", "city": 2})
            datablock = init_data.get("datablock", [])
            if not datablock or datablock[0] != 1:
                logger.warning(f"Helpers: Unerwartete Antwort von initlottery: {init_data}")
                return False

            # datablock[2] == 0 indicates free ticket is available
            if len(datablock) > 2 and datablock[2] == 0:
                logger.info("Helpers: Kostenloses Los verfügbar. Ziehe tägliches Los...")
                await self.client.api_call("city", {"mode": "newlot", "city": 2})
                prize_res = await self.client.api_call("city", {"mode": "lotgetprize", "city": 2})
                if prize_res.get("datablock", [None])[0] == 1:
                    logger.info("Helpers: Losbude-Gewinn erfolgreich abgeholt.")
                    return True
            else:
                logger.debug("Helpers: Losbude heute bereits besucht.")
        except UpstreamAPIError as e:
            logger.warning(f"Helpers: Fehler bei Losbude: {e}")

        return False

    async def handle_windmill(self) -> bool:
        """Harvest finished windmill products and start new recipe with available ingredients."""
        if not self.config.windmill:
            return False

        try:
            data = await self.client.api_call("city", {"mode": "windmillinit", "city": 2})
            datablock = data.get("datablock", [])
            if not datablock or datablock[0] != 1:
                return False

            status = datablock[4] if len(datablock) > 4 and isinstance(datablock[4], dict) else {}
            ready = int(status.get("ready", 0))
            running = int(status.get("running", 0))

            slot_dict = {}
            if len(datablock) > 2:
                raw_slots = datablock[2]
                if isinstance(raw_slots, dict):
                    slot_dict = raw_slots.get("1") or raw_slots.get(1) or {}
                elif isinstance(raw_slots, list):
                    if len(raw_slots) > 1 and isinstance(raw_slots[1], dict):
                        slot_dict = raw_slots[1]
                    elif len(raw_slots) > 0 and isinstance(raw_slots[0], dict):
                        slot_dict = raw_slots[0]
            slot_id = int(slot_dict.get("slot", 1))
            remain = int(slot_dict.get("remain", 0))

            # 1. Harvest if product is ready
            if ready == 1 and remain <= 0:
                logger.info(f"Helpers: Ernte fertiges Windmühlenprodukt aus Slot {slot_id}...")
                crop_res = await self.client.api_call(
                    "city", {"mode": "windmillcrop", "city": 2, "slot": slot_id}
                )
                if crop_res.get("datablock", [None])[0] == 1:
                    # Re-fetch windmill status
                    data = await self.client.api_call("city", {"mode": "windmillinit", "city": 2})
                    datablock = data.get("datablock", [])
                    status = (
                        datablock[4]
                        if len(datablock) > 4 and isinstance(datablock[4], dict)
                        else {}
                    )
                    ready = int(status.get("ready", 0))
                    running = int(status.get("running", 0))

            # 2. Start production if windmill is idle
            if (
                ready == 0
                and running == 0
                and len(datablock) > 1
                and isinstance(datablock[1], dict)
            ):
                formulas = [WindmillFormula(v) for v in datablock[1].values()]
                if not formulas:
                    return False

                # Shuffle to pick randomly among available recipes
                shuffled_formulas = list(formulas)
                random.shuffle(shuffled_formulas)

                for formula in shuffled_formulas:
                    if formula.id <= 0 or not formula.requirements:
                        continue
                    logger.debug(
                        f"Helpers: Prüfe Zutaten für Windmühlen-Rezept '{formula.name}'..."
                    )
                    can_produce = await self.stock_service.grasp_products(formula.requirements)
                    if can_produce:
                        logger.info(
                            f"Helpers: Starte Windmühlen-Produktion: '{formula.name}' in Slot {slot_id}..."
                        )
                        start_res = await self.client.api_call(
                            "city",
                            {
                                "mode": "windmillstartproduction",
                                "city": 2,
                                "slot": slot_id,
                                "formula": formula.id,
                            },
                        )
                        if start_res.get("datablock", [None])[0] == 1:
                            logger.info(
                                f"Helpers: Windmühlen-Produktion '{formula.name}' erfolgreich gestartet."
                            )
                            return True
                        else:
                            logger.warning(
                                f"Helpers: Starten der Windmühle fehlgeschlagen: {start_res}"
                            )

        except UpstreamAPIError as e:
            logger.warning(f"Helpers: Fehler bei Windmühle: {e}")

        return False

    async def handle_loginbonus(self, menue_data: dict[str, Any] | None = None) -> list[str]:
        """Claim unredeemed daily login bonus rewards (loginbonus_getreward)."""
        if not self.config.loginbonus:
            return []

        if menue_data is None:
            try:
                res = await self.client.api_call(
                    "farm", {"mode": "getfarms", "farm": 1, "position": 0}
                )
                menue_data = res.get("updateblock", {}).get("menue", {})
            except UpstreamAPIError as e:
                logger.warning(f"Helpers: Konnte Menü-Status für Loginbonus nicht abrufen: {e}")
                return []

        loginbonus = menue_data.get("loginbonus", {})
        if not isinstance(loginbonus, dict):
            return []

        config_rewards = loginbonus.get("config", {}).get("rewards", {})
        rewards_data = loginbonus.get("data", {}).get("rewards", {})
        if not isinstance(rewards_data, dict):
            return []

        claimed_days: list[str] = []
        for day_id, reward_info in rewards_data.items():
            if not isinstance(reward_info, dict):
                continue
            # If already claimed, "done" timestamp is present
            if "done" in reward_info:
                continue

            # Skip rewards of type 'plant' if configured (requires field space)
            if isinstance(config_rewards, dict):
                cfg_rew = config_rewards.get(str(day_id)) or config_rewards.get(day_id)
                if isinstance(cfg_rew, dict) and cfg_rew.get("type") == "plant":
                    logger.debug(f"Helpers: Überspringe Loginbonus Tag {day_id} (Typ 'plant').")
                    continue

            logger.info(f"Helpers: Hole täglichen Loginbonus für Tag {day_id} ab...")
            try:
                res = await self.client.api_call(
                    "farm", {"mode": "loginbonus_getreward", "day": day_id}
                )
                claimed_days.append(str(day_id))
                logger.info(f"Helpers: Loginbonus für Tag {day_id} erfolgreich abgeholt.")
                if isinstance(res, dict) and "updateblock" in res:
                    self.stock_service.update(res)
            except UpstreamAPIError as e:
                logger.warning(
                    f"Helpers: Fehler beim Abholen des Loginbonus für Tag {day_id}: {e}"
                )

        return claimed_days

    async def handle_greenhouse(self) -> bool:
        """Claim greenhouse botanical harvest bonus if cooldown expired (greenhouse_get_bonus)."""
        if not self.config.greenhouse:
            return False

        try:
            res = await self.client.api_call("farm", {"mode": "greenhouse_init"})
            datablock = res.get("datablock", {})
            gh_data = datablock.get("data") if isinstance(datablock, dict) else None
            if not gh_data or not isinstance(gh_data, dict):
                return False

            remain = gh_data.get("remain", 999999)
            if remain <= 0:
                logger.info("Helpers: Gewächshaus-Ertragsbonus ist bereit. Hole Bonus ab...")
                bonus_res = await self.client.api_call(
                    "farm", {"mode": "greenhouse_get_bonus"}
                )
                logger.info("Helpers: Gewächshaus-Ertragsbonus erfolgreich abgeholt.")
                if isinstance(bonus_res, dict) and "updateblock" in bonus_res:
                    self.stock_service.update(bonus_res)
                return True
            else:
                logger.debug(
                    f"Helpers: Gewächshaus-Bonus noch nicht bereit ({remain}s verbleibend)."
                )
                return False
        except UpstreamAPIError as e:
            logger.warning(f"Helpers: Fehler beim Gewächshaus-Bonus: {e}")
            return False

    async def handle_pet_parts(self, farmersmarket_data: dict[str, Any]) -> bool:
        """Buy and open daily free pet breeding parts package if available."""
        if not self.config.pet_parts:
            return False

        pets = farmersmarket_data.get("pets", {})
        if not isinstance(pets, dict):
            return False

        daily = int(pets.get("daily", 0) or 0)
        if daily <= 0:
            return False

        logger.info("Helpers: Hole tägliches Haustier-Zucht Bauteile-Päckchen ab...")
        try:
            await self.client.api_call("farm", {"mode": "pets_buy_parts", "id": 1, "amount": 1})
            open_res = await self.client.api_call("farm", {"mode": "pets_open_pack", "type": 1})
            logger.info("Helpers: Tägliches Haustier-Zucht Bauteile-Päckchen erfolgreich abgeholt und geöffnet.")
            if isinstance(open_res, dict) and "updateblock" in open_res:
                self.stock_service.update(open_res)
            return True
        except UpstreamAPIError as e:
            logger.warning(f"Helpers: Fehler beim Abholen des Haustier-Päckchens: {e}")
            return False

    async def serve(self) -> dict[str, Any]:
        """Execute full daily helpers and bonuses cycle."""
        logger.info("---------- HelpersService: Starte Zyklus ----------")
        results: dict[str, Any] = {
            "farm_dog": False,
            "donkey": False,
            "loginbonus": [],
            "lottery": False,
            "windmill": False,
            "greenhouse": False,
            "pet_parts": False,
        }

        # Fetch menue state for farm dog, donkey, and login bonus
        farms_data = None
        try:
            farms_data = await self.client.api_call(
                "farm", {"mode": "getfarms", "farm": 1, "position": 0}
            )
            menue = farms_data.get("updateblock", {}).get("menue", {})
            self.stock_service.update(farms_data)
        except UpstreamAPIError as e:
            logger.warning(f"Helpers: getfarms fehlgeschlagen: {e}")
            menue = {}

        fm_data = (
            farms_data.get("updateblock", {}).get("farmersmarket", {})
            if isinstance(farms_data, dict)
            else {}
        )

        results["farm_dog"] = await self.handle_farm_dog(menue)
        results["donkey"] = await self.handle_donkey(menue)
        results["loginbonus"] = await self.handle_loginbonus(menue)
        results["lottery"] = await self.handle_lottery()
        results["windmill"] = await self.handle_windmill()
        results["greenhouse"] = await self.handle_greenhouse()
        results["pet_parts"] = await self.handle_pet_parts(fm_data)

        return results
