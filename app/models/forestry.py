from pydantic import BaseModel, Field


class TreeSlot(BaseModel):
    """Represents one of the 25 tree plots in the forestry area."""

    position: int
    productid: int | None = None
    remain: int = 0
    waterremain: int = 0
    ready: int | None = None

    @property
    def is_empty(self) -> bool:
        return self.productid is None or self.productid == 0

    @property
    def is_ready_to_cut(self) -> bool:
        return not self.is_empty and self.remain <= 0

    @property
    def needs_water(self) -> bool:
        return not self.is_empty and self.waterremain <= 0


class ProductionSlot(BaseModel):
    """Represents a production slot in Sawmill or Carpentry."""

    slot_id: int
    productid: int | None = None
    remain: int = 0
    ready: int | None = None
    busy: bool = False

    @property
    def is_finished(self) -> bool:
        return self.busy and self.remain <= 0


class ForestryFactory(BaseModel):
    """Sawmill or Carpentry facility."""

    building_id: int  # 1 = Sawmill, 2 = Carpentry
    name: str
    slots: dict[int, ProductionSlot] = Field(default_factory=dict)


class FarmiOrder(BaseModel):
    """Visitor customer at the forestry hut."""

    farmi_id: int
    position: int
    products: dict[int, int] = Field(default_factory=dict)  # pid -> amount
    reward_points: int = 0
    reward_money: float = 0.0


class ForestryProduct(BaseModel):
    """Forestry item (seedling, trunk, board, furniture)."""

    pid: int
    name: str
    category: int  # 1: seedling/tree, 2: trunk, 3: sawn wood, 4: furniture
    amount: int = 0
    harvest: int = 1
    produces: int | None = None  # seedling produces this trunk pid
    required_products: list[tuple[int, int]] = Field(default_factory=list)  # list of (pid, amount)
