from typing import Any

from pydantic import BaseModel, Field, computed_field


class SushiRecipe(BaseModel):
    """Configuration and requirements for a single Sushi-Bar recipe."""

    pid: int
    name: str = ""
    level: int = 1
    category: str = "sushi"  # "sushi", "soup", "salad", "dessert"
    cost_money: int = 0
    cost_coins: int = 0
    duration: int = 0
    amount: int = 1
    points: int = 0
    eattime: int = 0
    needs: dict[int, int] = Field(default_factory=dict)
    reward: dict[str, Any] = Field(default_factory=dict)

    @computed_field
    @property
    def is_coin_recipe(self) -> bool:
        """Returns True if the recipe requires Coins (real currency) to cook."""
        return self.cost_coins > 0


class SushiProductionSlot(BaseModel):
    """Status of a kitchen cooking slot."""

    slot: int
    pid: int | None = None
    product_name: str = ""
    amount: int = 0
    startdate: int | None = None
    duration: int = 0
    remain: int = 0
    gone: int = 0
    block: int = 0  # 0 = unlocked, 1 = locked/not purchased

    @computed_field
    @property
    def is_unlocked(self) -> bool:
        return self.block == 0

    @computed_field
    @property
    def is_active(self) -> bool:
        return self.is_unlocked and self.pid is not None

    @computed_field
    @property
    def is_empty(self) -> bool:
        return self.is_unlocked and self.pid is None

    @computed_field
    @property
    def is_ready(self) -> bool:
        if not self.is_active:
            return False
        return self.remain <= 0 or (self.duration > 0 and self.gone >= self.duration)


class SushiTrainSlot(BaseModel):
    """Status of a slot on the sushi conveyor belt (train)."""

    slot: int
    pos: int = 0
    pid: int | None = None
    product_name: str = ""
    buy_time: int | None = None

    @computed_field
    @property
    def is_unlocked(self) -> bool:
        return self.buy_time is not None and self.buy_time > 0

    @computed_field
    @property
    def is_empty(self) -> bool:
        return self.is_unlocked and self.pid is None


class SushiFarmi(BaseModel):
    """Customer waiting or eating at the Sushi-Bar."""

    id: str
    slot: int
    img: str = ""
    need: dict[str, int] = Field(default_factory=dict)  # {"sushi": 3, "soup": 3}
    have: dict[str, int] = Field(default_factory=dict)  # {"soup": 3, "sushi": 2}
    eat_pid: int | None = None
    eat_product_name: str = ""
    eat_duration: int = 0
    eat_remain: int = 0
    finishdate: str = "0"
    createdate: str = "0"

    @computed_field
    @property
    def is_eating(self) -> bool:
        return self.eat_remain > 0

    @computed_field
    @property
    def is_satisfied(self) -> bool:
        """True if Farmi received all requested dishes in all categories."""
        if not self.need:
            return False
        return all(self.have.get(cat, 0) >= amount for cat, amount in self.need.items())

    @computed_field
    @property
    def is_ready_to_cash(self) -> bool:
        """True if Farmi received everything and has finished eating (can be checked out)."""
        return self.is_satisfied and not self.is_eating


class SushiQuestTarget(BaseModel):
    """Identified Questreihe 5 target requirement for sushi production."""

    quest_id: int
    pid: int
    product_name: str
    amount_needed: int
    amount_in_stock: int
    missing: int
    category: str


class SushiBarSummary(BaseModel):
    """Aggregated status overview for REST API and dashboard."""

    level: int = 1
    level_percent: float = 0.0
    level_rest: int = 0
    farm: int = 8
    position: int = 2
    production_slots: list[SushiProductionSlot] = Field(default_factory=list)
    train_slots: list[SushiTrainSlot] = Field(default_factory=list)
    farmis: list[SushiFarmi] = Field(default_factory=list)
    quest5_target: SushiQuestTarget | None = None
    enabled: bool = True
    auto_harvest: bool = True
    auto_produce: bool = True
    auto_train: bool = False
    auto_farmi: bool = True
    production_strategy: str = "quest5"
