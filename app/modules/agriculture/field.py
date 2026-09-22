import time
from typing import Any

from loguru import logger

from app.core.client import MFFGameClient
from app.models.farm import PlantTile
from app.models.product import Product


class Field:
    """Represents a 120-tile agricultural field on a specific farm."""

    def __init__(
        self,
        client: MFFGameClient,
        farm_id: int,
        position: int,
        name: str = "Acker",
        harvest_mode: str = "all",
        auto_water: bool = True,
        auto_crop: bool = True,
    ):
        self.client = client
        self.farm_id = farm_id
        self.position = position
        self.name = name
        self.harvest_mode = harvest_mode
        self.auto_water = auto_water
        self.auto_crop = auto_crop
        self.tiles: list[PlantTile] = []

    @property
    def is_fully_planted(self) -> bool:
        return len(self.tiles) >= 120

    @property
    def has_ready_crops(self) -> bool:
        return any(tile.phase == 4 for tile in self.tiles)

    @property
    def all_crops_ready(self) -> bool:
        return len(self.tiles) > 0 and all(tile.phase == 4 for tile in self.tiles)

    @property
    def ready_crops_count(self) -> int:
        return sum(1 for tile in self.tiles if tile.phase == 4)

    @property
    def needs_watering(self) -> bool:
        return any(not tile.is_watered for tile in self.tiles)

    @property
    def unwatered_count(self) -> int:
        return sum(1 for tile in self.tiles if not tile.is_watered)

    async def update(self, body: dict[str, Any] | None = None) -> list[PlantTile]:
        """Fetch current garden status and update tile models."""
        if not body:
            body = await self.client.api_call(
                "farm",
                {"mode": "gardeninit", "farm": self.farm_id, "position": self.position},
            )

        datablock = body.get("datablock")
        if not datablock:
            return self.tiles

        # Check status (handles list [1, ...] or dict {'0': 1, ...})
        status = None
        if isinstance(datablock, (list, tuple)) and len(datablock) > 0:
            status = datablock[0]
        elif isinstance(datablock, dict):
            status = datablock.get(0) if 0 in datablock else datablock.get("0")

        if status != 1:
            logger.warning(
                f"gardeninit für Feld {self.farm_id}/{self.position} lieferte Status != 1 ({status})."
            )
            return self.tiles

        # Normalise tile block: datablock[1] or datablock[3][1] or dict keys
        raw_tiles = {}
        if isinstance(datablock, (list, tuple)):
            raw_tiles = (
                datablock[1]
                if len(datablock) > 1 and isinstance(datablock[1], dict)
                else (
                    datablock[3][1]
                    if len(datablock) > 3 and isinstance(datablock[3], list) and len(datablock[3]) > 1
                    else {}
                )
            )
        elif isinstance(datablock, dict):
            if "1" in datablock and isinstance(datablock["1"], dict):
                raw_tiles = datablock["1"]
            elif 1 in datablock and isinstance(datablock[1], dict):
                raw_tiles = datablock[1]
            elif "5" in datablock and isinstance(datablock["5"], list) and len(datablock["5"]) > 1 and isinstance(datablock["5"][1], dict):
                raw_tiles = datablock["5"][1]

        self.tiles = []
        if isinstance(raw_tiles, dict):
            now_ts = int(time.time())
            for tile_id_str, tile_data in raw_tiles.items():
                if (
                    str(tile_id_str).isdigit()
                    and int(tile_id_str) > 0
                    and isinstance(tile_data, dict)
                ):
                    tile_id = int(tile_id_str)
                    pid = int(
                        tile_data.get("inhalt", 0)
                        or tile_data.get("harvest", 0)
                        or tile_data.get("pid", 0)
                    )
                    phase = int(tile_data.get("phase", 0))

                    remain = int(tile_data.get("remain", 0))
                    if remain == 0 and "zeit" in tile_data:
                        zeit_val = int(tile_data.get("zeit", 0))
                        if zeit_val > now_ts:
                            remain = zeit_val - now_ts

                    iswater_val = tile_data.get("iswater", 0)
                    is_watered = (
                        bool(int(iswater_val))
                        if isinstance(iswater_val, (int, str))
                        else bool(iswater_val)
                    )
                    cat = str(tile_data.get("category") or tile_data.get("buildingid", "v"))

                    self.tiles.append(
                        PlantTile(
                            tile_id=tile_id,
                            pid=pid,
                            phase=phase,
                            remain_seconds=remain,
                            is_watered=is_watered,
                            category=cat,
                        )
                    )

        return self.tiles

    async def crop(self) -> bool:
        """Harvest ready crops based on configured harvest_mode."""
        if not self.auto_crop:
            return False

        ready = self.all_crops_ready if self.harvest_mode == "all" else self.has_ready_crops

        if ready and self.ready_crops_count > 0:
            logger.info(
                f"Feld {self.farm_id}/{self.position}: Ernte {self.ready_crops_count} reife Pflanzen ({self.harvest_mode})..."
            )
            body = await self.client.api_call(
                "farm",
                {"mode": "cropgarden", "farm": self.farm_id, "position": self.position},
            )
            await self.update(body)
            return True

        return False

    async def plant(self, plant: Product) -> bool:
        """Plant candidate crop on all free field tiles using autoplant."""
        if len(self.tiles) < 120:
            logger.info(
                f"Feld {self.farm_id}/{self.position}: Säe '{plant.name}' (PID {plant.pid}) auf {120 - len(self.tiles)} freie Kacheln..."
            )
            body = await self.client.api_call(
                "farm",
                {
                    "mode": "autoplant",
                    "farm": self.farm_id,
                    "position": self.position,
                    "id": plant.pid,
                    "product": plant.pid,
                },
            )
            datablock = body.get("datablock")
            if isinstance(datablock, list) and len(datablock) > 0 and datablock[0] == 0:
                err_msg = datablock[1] if len(datablock) > 1 else "Unbekannter Fehler"
                logger.warning(
                    f"Feld {self.farm_id}/{self.position}: Säen von '{plant.name}' fehlgeschlagen: {err_msg}"
                )
                return False

            await self.update(body)
            return True

        return False

    async def water(self) -> bool:
        """Water unwatered tiles if auto_water is enabled."""
        if not self.auto_water:
            return False

        if self.needs_watering and len(self.tiles) > 0:
            logger.info(
                f"Feld {self.farm_id}/{self.position}: Gieße {self.unwatered_count} Kacheln..."
            )
            body = await self.client.api_call(
                "farm",
                {"mode": "watergarden", "farm": self.farm_id, "position": self.position},
            )
            await self.update(body)
            return True

        return False

    async def serve(self, plant_candidate: Product | None = None) -> bool:
        """Execute full field lifecycle: update -> crop -> plant -> water."""
        logger.info(f"--- Feld {self.farm_id}/{self.position} ({self.name}) wird bedient ---")
        await self.update()
        cropped = await self.crop()
        if cropped:
            await self.update()
        planted = False
        if plant_candidate:
            planted = await self.plant(plant_candidate)
        await self.water()
        return cropped or planted
