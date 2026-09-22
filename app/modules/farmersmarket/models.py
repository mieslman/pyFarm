"""Pydantic v2 domain models for Farmers Market (Bauernmarkt Dorf 2)."""

from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Gärtnerei (Nursery) Models
# ---------------------------------------------------------------------------

class NurserySlot(BaseModel):
    """A production slot in the flower arrangement nursery."""

    slot_id: int
    pid: int | None = None
    duration: int = 0
    createdate: int = 0
    remain: int = 0
    block: int = 0
    cost: int = 0
    coins: int = 0

    @property
    def is_blocked(self) -> bool:
        """Slot is locked or requires coins."""
        return self.block == 1 or self.coins > 0

    @property
    def is_ready(self) -> bool:
        """Arrangement is fully produced and ready to harvest."""
        return not self.is_blocked and self.pid is not None and self.remain <= 0

    @property
    def is_free(self) -> bool:
        """Slot is available to start a new production."""
        return not self.is_blocked and (self.pid is None or self.remain <= 0)


class NurseryProduct(BaseModel):
    """Flower arrangement recipe definition."""

    id: int
    pid: int
    name: str = ""
    farmipoints: int = 0
    duration: int = 0
    crop: int = 0
    cost: int = 0
    coins: int = 0
    requirements: dict[int, int] = Field(default_factory=dict)


class NurseryState(BaseModel):
    """Aggregated state of the nursery."""

    slots: dict[int, NurserySlot] = Field(default_factory=dict)
    products: dict[int, NurseryProduct] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Blumenwiese (Flower Area) Models
# ---------------------------------------------------------------------------

class FlowerField(BaseModel):
    """A single flower bed on the 36-field flower area."""

    pos: int  # 1..36
    pid: int | None = None
    remain: int = 0
    water_remain: int = 0
    duration: int = 0
    createdate: int = 0

    @property
    def is_empty(self) -> bool:
        """Field has nothing planted."""
        return self.pid is None or self.pid <= 0

    @property
    def is_ready(self) -> bool:
        """Flower is fully grown and ready to harvest."""
        return not self.is_empty and self.remain <= 0

    @property
    def needs_water(self) -> bool:
        """Flower needs watering to grow faster."""
        return not self.is_empty and self.water_remain <= 0 and self.remain > 0


class FlowerAreaState(BaseModel):
    """State of all 36 flower beds in Dorf 2."""

    fields: dict[int, FlowerField] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Schau-Slots (Flower Slots) Models
# ---------------------------------------------------------------------------

class FlowerSlotItem(BaseModel):
    """An arrangement placed on display in Dorf 2."""

    slot_id: int
    pid: int | None = None
    points: int = 0
    remain: int = 0
    waterremain: int = 0

    @property
    def is_expired(self) -> bool:
        """Arrangement has expired and should be removed."""
        return self.pid is not None and self.remain <= 0

    @property
    def needs_water(self) -> bool:
        """Arrangement display needs watering."""
        return self.pid is not None and self.remain > 0 and self.waterremain <= 0

    @property
    def is_empty(self) -> bool:
        """Display slot is empty."""
        return self.pid is None or self.pid <= 0


class FlowerSlotsState(BaseModel):
    """State of display arrangement slots."""

    slots: dict[int, FlowerSlotItem] = Field(default_factory=dict)
    total_points: int = 0


# ---------------------------------------------------------------------------
# Farmis (Kunden am Bauernmarkt) Models
# ---------------------------------------------------------------------------

class CartItem(BaseModel):
    """A product demanded by a Farmi customer."""

    pid: int
    amount: int


class MarketFarmi(BaseModel):
    """A customer visiting the flower market."""

    id: str
    price: int = 0
    points: int = 0
    cart: list[CartItem] = Field(default_factory=list)
    status: int = 0  # 0: waiting


# ---------------------------------------------------------------------------
# Tierzucht (Pet Breed) Models
# ---------------------------------------------------------------------------

class PetBreedSlot(BaseModel):
    """A breeding slot."""

    slot_id: int
    duration: int = 0
    gone: int = 0
    tool_id: int | None = None
    block: int = 0
    coins: int = 0

    @property
    def is_blocked(self) -> bool:
        return self.block == 1 or self.coins > 0

    @property
    def is_ready(self) -> bool:
        return not self.is_blocked and self.tool_id is not None and self.duration > 0 and self.gone >= self.duration


class PetBreedTool(BaseModel):
    """Tool required for breeding."""

    tool_id: int
    type: str = ""
    needs: dict[int, int] = Field(default_factory=dict)


class PetBreedQuest(BaseModel):
    """Active breeding quest."""

    quest_id: str | None = None
    remain: int = 0
    products: dict[int, int] = Field(default_factory=dict)


class PetBreedState(BaseModel):
    """State of pet breeding."""

    slots: dict[int, PetBreedSlot] = Field(default_factory=dict)
    quest: PetBreedQuest | None = None
    tools: dict[int, PetBreedTool] = Field(default_factory=dict)
    daily: int = 0


# ---------------------------------------------------------------------------
# Aggregated Summary (API & Dashboard)
# ---------------------------------------------------------------------------

class FarmersMarketSummary(BaseModel):
    """Live summary of all farmersmarket domains for REST API & Dashboard."""

    enabled: bool = True
    nursery_ready_count: int = 0
    nursery_active_count: int = 0
    flower_fields_total: int = 36
    flower_fields_ready: int = 0
    flower_fields_growing: int = 0
    flower_fields_empty: int = 0
    flower_slots_active: int = 0
    farmis_waiting: int = 0
    farmis_served_total: int = 0
    pet_breed_enabled: bool = False
    pet_breed_status: str = "Inaktiv (Konfiguration)"
    details: dict[str, Any] = Field(default_factory=dict)
