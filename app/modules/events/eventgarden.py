from typing import Any, Optional
from loguru import logger

from app.core.client import MFFGameClient
from app.modules.events.models import EventGardenStatus, EventGardenTile


class EventGardenService:
    """Manages the temporary event garden: harvesting and auto-planting."""

    def __init__(self, client: MFFGameClient):
        self.client = client
        self.last_status: Optional[EventGardenStatus] = None

    async def get_status(self) -> Optional[EventGardenStatus]:
        """Fetch and parse event garden state."""
        try:
            res = await self.client.api_call("farm", {"mode": "eventgarden_init"})
            datablock = res.get("datablock", {})
            if not isinstance(datablock, dict) or not datablock.get("data"):
                return None

            tiles = datablock.get("data", {}).get("tiles", {})
            stock = datablock.get("data", {}).get("stock", {})

            ripe_count = 0
            for tile_data in tiles.values():
                if isinstance(tile_data, dict) and tile_data.get("remain", 0) <= 0:
                    ripe_count += 1

            available_seeds = {
                int(pid): int(amt) for pid, amt in stock.items() if str(pid).isdigit() and int(amt) > 0
            }

            status = EventGardenStatus(
                tiles_count=len(tiles),
                ripe_count=ripe_count,
                available_seeds=available_seeds,
            )
            self.last_status = status
            return status

        except Exception as e:
            logger.warning(f"EventGardenService: Fehler beim Abruf von eventgarden_init: {e}")
            return None

    async def serve(self) -> bool:
        """Execute event garden harvest and re-planting cycle."""
        try:
            res = await self.client.api_call("farm", {"mode": "eventgarden_init"})
            datablock = res.get("datablock", {})
            if not isinstance(datablock, dict) or not datablock.get("data"):
                logger.debug("EventGardenService: Kein aktiver Event-Garten vorhanden.")
                return False

            tiles = datablock.get("data", {}).get("tiles", {})
            stock = datablock.get("data", {}).get("stock", {})
            products = datablock.get("config", {}).get("products", {})

            action_performed = False

            # 1. Harvest if all planted tiles are ripe (no tiles with remain > 0)
            growing_tiles = [
                t for t in tiles.values() if isinstance(t, dict) and t.get("remain", 0) > 0
            ]
            if tiles and len(growing_tiles) == 0:
                logger.info(f"EventGardenService: Ernte {len(tiles)} reife Beete im Event-Garten ab...")
                harvest_res = await self.client.api_call("farm", {"mode": "eventgarden_harvest_all"})
                if harvest_res.get("datablock", {}).get("data"):
                    logger.info("EventGardenService: Event-Garten erfolgreich abgeerntet.")
                    action_performed = True
                    # Update data after harvest
                    res = harvest_res
                    datablock = res.get("datablock", {})
                    tiles = datablock.get("data", {}).get("tiles", {})
                    stock = datablock.get("data", {}).get("stock", {})
                else:
                    logger.warning(f"EventGardenService: Fehler beim Abernten: {harvest_res}")

            # 2. Plant if garden is empty
            if len(tiles) == 0:
                # Find available seed with largest stock
                valid_seeds: list[tuple[int, int, str]] = []
                for pid_str, amt in stock.items():
                    if str(pid_str).isdigit() and int(amt) > 0:
                        pid = int(pid_str)
                        prod_name = products.get(pid_str, {}).get("name", f"PID {pid}")
                        valid_seeds.append((pid, int(amt), prod_name))

                valid_seeds.sort(key=lambda s: s[1], reverse=True)

                if valid_seeds:
                    best_pid, best_amt, best_name = valid_seeds[0]
                    logger.info(
                        f"EventGardenService: Bepflanze Event-Garten mit '{best_name}' "
                        f"(PID {best_pid}, Vorrat: {best_amt} Stk)..."
                    )
                    plant_res = await self.client.api_call(
                        "farm",
                        {"mode": "eventgarden_autoplant", "plant": best_pid},
                    )
                    if plant_res.get("datablock", {}).get("data"):
                        logger.info(f"EventGardenService: Event-Garten erfolgreich bepflanzt mit {best_name}.")
                        action_performed = True
                    else:
                        logger.warning(f"EventGardenService: Fehler beim Bepflanzen: {plant_res}")
                else:
                    logger.debug("EventGardenService: Kein Event-Saatgut im Vorrat vorhanden.")

            await self.get_status()
            return action_performed

        except Exception as e:
            logger.error(f"EventGardenService: Unerwarteter Fehler im Event-Garten-Zyklus: {e}")
            return False
