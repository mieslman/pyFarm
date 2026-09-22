from typing import Any, Optional
from pydantic import BaseModel, Field


class InsectNicheSlot(BaseModel):
    """Represents a living niche/slot for a specific insect species in the hotel."""
    slot_id: str
    name: str = ""
    level: int = 1
    population: int = 0
    happiness: float = 0.0


class InsectStockSlot(BaseModel):
    """Represents a feeding stock compartment in the hotel's food storage."""
    slot_id: str
    level: int = 1
    pid: Optional[int] = None
    product_name: str = ""
    amount: int = 0
    capacity: int = 0

    @property
    def is_empty(self) -> bool:
        return self.amount <= 0

    @property
    def missing_amount(self) -> int:
        return max(0, self.capacity - self.amount)

    @property
    def fill_ratio(self) -> float:
        if self.capacity <= 0:
            return 1.0
        return self.amount / float(self.capacity)


class InsectCheckout(BaseModel):
    """The hotel's cash register collecting earned kT and XP."""
    level: int = 1
    money: float = 0.0
    points: int = 0
    limit_money: float = 0.0
    limit_points: int = 0

    @property
    def fill_ratio_money(self) -> float:
        if self.limit_money <= 0:
            return 0.0
        return self.money / float(self.limit_money)

    @property
    def fill_ratio_points(self) -> float:
        if self.limit_points <= 0:
            return 0.0
        return self.points / float(self.limit_points)

    @property
    def is_collectible(self) -> bool:
        return self.money > 0 or self.points > 0


class InsectHotelSnapshot(BaseModel):
    """Complete runtime state snapshot of the Insect Hotel."""
    id: str = ""
    slots: dict[str, InsectNicheSlot] = Field(default_factory=dict)
    stock_slots: dict[str, InsectStockSlot] = Field(default_factory=dict)
    checkout: InsectCheckout = Field(default_factory=InsectCheckout)
    total_population: int = 0
    last_updated: Optional[str] = None


class InsectHotelSummary(BaseModel):
    """Consolidated summary for dashboard and REST API reporting."""
    active: bool = False
    hotel_id: str = ""
    total_population: int = 0
    slots_count: int = 0
    stock_slots_count: int = 0
    checkout_money: float = 0.0
    checkout_points: int = 0
    checkout_money_limit: float = 0.0
    checkout_points_limit: int = 0
    last_updated: Optional[str] = None
