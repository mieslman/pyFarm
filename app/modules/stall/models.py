from typing import Optional
from pydantic import BaseModel, Field


class StallSlot(BaseModel):
    """Represents an individual display slot in a market stall."""

    slot_id: str
    pid: Optional[int] = None
    product_name: str = "Leer"
    amount: int = 0
    time: Optional[int] = None

    @property
    def is_empty(self) -> bool:
        return self.pid is None or self.amount <= 0


class MarketStall(BaseModel):
    """Represents a market stall at a specific village position."""

    position: str
    level: int = 1
    fillsum: int = 100
    reward_ready: bool = False
    points: int = 0
    farmi_count: int = 0
    slots: dict[str, StallSlot] = Field(default_factory=dict)

    @property
    def slots_count(self) -> int:
        return len(self.slots)

    @property
    def filled_slots_count(self) -> int:
        return sum(1 for s in self.slots.values() if not s.is_empty)


class StallSnapshot(BaseModel):
    """Complete runtime snapshot of all market stalls."""

    stalls: dict[str, MarketStall] = Field(default_factory=dict)
    last_updated: str


class StallSummary(BaseModel):
    """Compact status report for REST API and dashboard."""

    active: bool = False
    stalls_count: int = 0
    total_slots: int = 0
    filled_slots: int = 0
    rewards_ready_count: int = 0
    last_updated: Optional[str] = None
