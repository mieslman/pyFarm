from pydantic import BaseModel, Field


class PlantTile(BaseModel):
    """Represents a single tile or planted item on an agricultural field."""

    tile_id: int
    pid: int
    phase: int = Field(description="1..3=growing, 4=ready to crop")
    remain_seconds: int = 0
    is_watered: bool = False
    category: str = "v"


class FarmField(BaseModel):
    """Represents an agricultural field (120 tiles) on a farm."""

    farm_id: int
    position: int
    name: str = "Acker"
    building_id: int = 1
    tiles: list[PlantTile] = Field(default_factory=list)

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


class BarnData(BaseModel):
    """Represents an animal barn (Shed) state from inner_init."""

    farm_id: int
    position: int
    building_id: int = Field(description="Building ID (e.g. 2=Hühnerstall, 3=Kuhstall)")
    product_id: int = Field(default=0, description="Produced product PID (e.g. 9=Ei, 10=Milch)")
    animals_count: int = Field(default=0, description="Number of animals currently housed")
    remain_seconds: int = Field(default=0, description="Remaining production time")
    rest_seconds: int = Field(default=0, description="Total cycle time")
    # Feed options: pid -> feeding duration/time per unit
    feed_options: dict[int, int] = Field(default_factory=dict)
