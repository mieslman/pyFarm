from typing import Any, Optional
from loguru import logger

from app.core.client import MFFGameClient
from app.modules.events.models import PentecostStatus


class PentecostEventService:
    """Manages the Pentecost event: watering and fertilizing the event plant."""

    def __init__(self, client: MFFGameClient):
        self.client = client
        self.last_status: Optional[PentecostStatus] = None

    async def get_status(self) -> Optional[PentecostStatus]:
        """Fetch and parse Pentecost event status."""
        try:
            res = await self.client.api_call("farm", {"mode": "pentecostevent_init"})
            datablock = res.get("datablock", {})
            if not isinstance(datablock, dict) or "data" not in datablock:
                return None

            exchange = datablock.get("config", {}).get("exchange", {})
            req_water = int(exchange.get("water", {}).get("amount", 0))
            req_fert = int(exchange.get("fertilizer", {}).get("amount", 0))

            data_block = datablock.get("data", {})
            avail_water = int(data_block.get("water", 0))
            avail_fert = int(data_block.get("fertilizer", 0))
            w_remain = int(data_block.get("water_remain", 0))
            f_remain = int(data_block.get("fertilizer_remain", 0))

            status = PentecostStatus(
                water_remain=max(0, w_remain),
                fertilizer_remain=max(0, f_remain),
                available_water=avail_water,
                available_fertilizer=avail_fert,
                required_water=req_water,
                required_fertilizer=req_fert,
            )
            self.last_status = status
            return status

        except Exception as e:
            logger.warning(f"PentecostEventService: Fehler beim Abruf von pentecostevent_init: {e}")
            return None

    async def serve(self) -> bool:
        """Check care requirements and water/fertilize if cooldown expired."""
        try:
            res = await self.client.api_call("farm", {"mode": "pentecostevent_init"})
            datablock = res.get("datablock", {})
            if not isinstance(datablock, dict) or "data" not in datablock:
                logger.debug("PentecostEventService: Kein aktives Pfingst-Event vorhanden.")
                return False

            exchange = datablock.get("config", {}).get("exchange", {})
            req_water = int(exchange.get("water", {}).get("amount", 0))
            req_fert = int(exchange.get("fertilizer", {}).get("amount", 0))

            data_block = datablock.get("data", {})
            avail_water = int(data_block.get("water", 0))
            avail_fert = int(data_block.get("fertilizer", 0))
            w_remain = data_block.get("water_remain", 0)
            f_remain = data_block.get("fertilizer_remain", 0)

            action_done = False

            # 1. Water
            if avail_water >= req_water and (w_remain is None or int(w_remain) <= 0):
                logger.info(f"PentecostEventService: Gieße Pfingstpflanze ({req_water} Wasser)...")
                water_res = await self.client.api_call(
                    "farm",
                    {"mode": "pentecostevent_care", "type": "water"},
                )
                if water_res.get("datablock"):
                    logger.info("PentecostEventService: Pfingstpflanze erfolgreich gegossen.")
                    action_done = True
                else:
                    logger.warning(f"PentecostEventService: Fehler beim Gießen: {water_res}")

            # 2. Fertilizer
            if avail_fert >= req_fert and (f_remain is None or int(f_remain) <= 0):
                logger.info(f"PentecostEventService: Dünge Pfingstpflanze ({req_fert} Dünger)...")
                fert_res = await self.client.api_call(
                    "farm",
                    {"mode": "pentecostevent_care", "type": "fertilizer"},
                )
                if fert_res.get("datablock"):
                    logger.info("PentecostEventService: Pfingstpflanze erfolgreich gedüngt.")
                    action_done = True
                else:
                    logger.warning(f"PentecostEventService: Fehler beim Düngen: {fert_res}")

            await self.get_status()
            return action_done

        except Exception as e:
            logger.error(f"PentecostEventService: Unerwarteter Fehler im Pfingst-Zyklus: {e}")
            return False
