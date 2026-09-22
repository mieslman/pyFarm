"""Pydantic v2 data models for Foodworld (Picknick-Bereich / Restaurant)."""

from typing import Any

from pydantic import BaseModel, Field


class FoodworldSlot(BaseModel):
    """Represents a production slot in a Foodworld kitchen building."""

    slot_id: int
    pid: int | None = None
    amount: int = 0
    remain: int = 0
    ready: int = 0
    cost: int = 0
    coins: int = 0
    block: int = 0

    @property
    def is_blocked(self) -> bool:
        """Slot is blocked or requires coins to unlock."""
        return self.block == 1 or self.coins > 0

    @property
    def is_ready(self) -> bool:
        """Finished dish ready to be picked up."""
        return self.ready == 1 or (self.pid is not None and self.remain <= 0)

    @property
    def is_free(self) -> bool:
        """Slot is unlocked and has no active production."""
        return self.pid is None and not self.is_blocked


class FoodworldBuilding(BaseModel):
    """Represents one of the 4 kitchen stations (Getränke, Imbiss, Konditorei, Eisdiele)."""

    id: int
    name: str = ""
    level: int = 1
    slots: dict[int, FoodworldSlot] = Field(default_factory=dict)

    @property
    def ready_slots(self) -> list[FoodworldSlot]:
        return [s for s in self.slots.values() if s.is_ready]

    @property
    def free_slots(self) -> list[FoodworldSlot]:
        return [s for s in self.slots.values() if s.is_free]


class TableChair(BaseModel):
    """Represents an individual chair at a restaurant table."""

    chair_id: int
    farmi_id: str | None = None
    remain: int = 0
    ready: int = 0

    @property
    def is_ready(self) -> bool:
        """Guest has finished eating and can be cashed out."""
        return self.ready == 1 or (self.farmi_id is not None and self.remain <= 0)

    @property
    def is_free(self) -> bool:
        """Chair is currently unoccupied."""
        return self.farmi_id is None and self.ready == 0


class TableGroup(BaseModel):
    """Represents a restaurant table with 4 chairs."""

    table_id: int
    chairs: dict[int, TableChair] = Field(default_factory=dict)
    block: int = 0
    locked: int = 0

    @property
    def is_unlocked(self) -> bool:
        """Table is available for seating guests."""
        return self.block == 0 and self.locked == 0

    @property
    def free_chairs(self) -> list[TableChair]:
        if not self.is_unlocked:
            return []
        return [c for c in self.chairs.values() if c.is_free]

    @property
    def ready_chairs(self) -> list[TableChair]:
        return [c for c in self.chairs.values() if c.is_ready]


class FoodworldFarmi(BaseModel):
    """Represents a waiting or seated customer in the restaurant."""

    id: str
    price: int = 0  # Total offer in kT
    points: int = 0
    status: int = 0  # 0 = waiting, 1 = seated
    cart: dict[int, int] = Field(default_factory=dict)  # pid -> amount


class FoodworldRecipe(BaseModel):
    """Represents a recipe in the Foodworld kitchens."""

    id: int
    building_id: int
    output_pid: int
    output_amount: int = 1
    requirements: dict[int, int] = Field(default_factory=dict)  # input_pid -> needed_amount
    star: int = 0


class FoodworldSummary(BaseModel):
    """Structured live summary for REST API and Web Dashboard."""

    enabled: bool = True
    kitchen_slots_total: int = 0
    kitchen_slots_active: int = 0
    kitchen_slots_ready: int = 0
    tables_unlocked: int = 0
    chairs_total: int = 0
    chairs_occupied: int = 0
    chairs_ready: int = 0
    farmis_waiting: int = 0
    dishes_exported_count: int = 0
    revenue_collected_kt: float = 0.0
    details: dict[str, Any] = Field(default_factory=dict)
