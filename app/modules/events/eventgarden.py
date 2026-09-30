from typing import Any

from loguru import logger

from app.core.client import MFFGameClient
from app.modules.events.models import EventGardenStatus


class EventGardenService:
    """Manages the temporary event garden: harvesting and auto-planting."""

    def __init__(self, client: MFFGameClient):
        self.client = client
        self.last_status: EventGardenStatus | None = None

    @staticmethod
    def _extract_tiles(datablock: dict[str, Any]) -> dict[str, Any]:
        """Safely extract tiles mapping regardless of whether upstream returned list or dict."""
        raw_tiles = datablock.get("data", {}).get("tiles", {})
        if isinstance(raw_tiles, dict):
            return raw_tiles
        if isinstance(raw_tiles, list):
            return {
                str(idx): item
                for idx, item in enumerate(raw_tiles, start=1)
                if isinstance(item, dict)
            }
        return {}

    @staticmethod
    def _extract_stock(datablock: dict[str, Any]) -> dict[str, int]:
        """Safely extract stock mapping regardless of upstream type."""
        raw_stock = datablock.get("data", {}).get("stock", {})
        if not isinstance(raw_stock, dict):
            return {}
        result: dict[str, int] = {}
        for k, v in raw_stock.items():
            try:
                amt = int(v)
                if amt > 0:
                    result[str(k)] = amt
            except (ValueError, TypeError):
                continue
        return result

    async def get_status(self) -> EventGardenStatus | None:
        """Fetch and parse event garden state."""
        try:
            res = await self.client.api_call("farm", {"mode": "eventgarden_init"})
            datablock = res.get("datablock", {})
            if not isinstance(datablock, dict) or not datablock.get("data"):
                return None

            tiles = self._extract_tiles(datablock)
            stock = self._extract_stock(datablock)

            ripe_count = 0
            for tile_data in tiles.values():
                if isinstance(tile_data, dict) and tile_data.get("remain", 0) <= 0:
                    ripe_count += 1

            status = EventGardenStatus(
                tiles_count=len(tiles),
                ripe_count=ripe_count,
                available_seeds=stock,
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

            tiles = self._extract_tiles(datablock)
            stock = self._extract_stock(datablock)
            products = datablock.get("config", {}).get("products", {})
            if not isinstance(products, dict):
                products = {}

            action_performed = False

            # 1. Harvest if all planted tiles are ripe (no tiles with remain > 0)
            growing_tiles = [
                t for t in tiles.values() if isinstance(t, dict) and t.get("remain", 0) > 0
            ]
            if tiles and len(growing_tiles) == 0:
                logger.info(
                    f"EventGardenService: Ernte {len(tiles)} reife Beete im Event-Garten ab..."
                )
                harvest_res = await self.client.api_call(
                    "farm", {"mode": "eventgarden_harvest_all"}
                )
                if harvest_res.get("datablock", {}).get("data"):
                    logger.info("EventGardenService: Event-Garten erfolgreich abgeerntet.")
                    action_performed = True
                    # Update data after harvest
                    datablock = harvest_res.get("datablock", {})
                    tiles = self._extract_tiles(datablock)
                    stock = self._extract_stock(datablock)
                else:
                    logger.warning(f"EventGardenService: Fehler beim Abernten: {harvest_res}")

            # 2. Plant if garden is empty
            if len(tiles) == 0:
                # Find available seed with largest stock
                valid_seeds: list[tuple[str, int, str]] = []
                for pid_str, amt in stock.items():
                    if amt > 0:
                        prod_name = products.get(pid_str, {}).get("name", f"PID {pid_str}")
                        valid_seeds.append((pid_str, amt, prod_name))

                valid_seeds.sort(key=lambda s: s[1], reverse=True)

                if valid_seeds:
                    best_pid_str, best_amt, best_name = valid_seeds[0]
                    plant_param: int | str = (
                        int(best_pid_str) if best_pid_str.isdigit() else best_pid_str
                    )
                    logger.info(
                        f"EventGardenService: Bepflanze Event-Garten mit '{best_name}' "
                        f"(Saatgut: {plant_param}, Vorrat: {best_amt} Stk)..."
                    )
                    plant_res = await self.client.api_call(
                        "farm",
                        {"mode": "eventgarden_autoplant", "plant": plant_param},
                    )
                    if plant_res.get("datablock", {}).get("data"):
                        logger.info(
                            f"EventGardenService: Event-Garten erfolgreich bepflanzt mit {best_name}."
                        )
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
