from pydantic import BaseModel, computed_field


class FactoryIngredient(BaseModel):
    """Ingredient required for factory production."""

    pid: int
    amount: int
    name: str = ""


class FactoryRecipe(BaseModel):
    """Production recipe available in a factory."""

    item_id: str
    output_pid: int
    output_amount: int
    name: str = ""
    duration: int = 0
    points: int = 0
    cost_coins: int = 0
    min_level: int = 0
    ingredients: list[FactoryIngredient] = []


class FactorySlot(BaseModel):
    """Single production slot in a factory."""

    slot_id: int
    is_blocked: bool = False
    is_empty: bool = True
    is_ready: bool = False
    pid: int | None = None
    product_name: str = ""
    amount: int | None = None
    remain_seconds: int | None = None

    @computed_field
    @property
    def is_active(self) -> bool:
        """True if the slot is currently producing and not yet ready."""
        return bool(self.pid and not self.is_ready and not self.is_empty and not self.is_blocked)


class FactoryBuildingData(BaseModel):
    """Summary and runtime state of a factory building."""

    farm_id: int
    position: int
    building_id: int
    name: str
    level: int = 1
    slots: list[FactorySlot] = []
    recipes: list[FactoryRecipe] = []
