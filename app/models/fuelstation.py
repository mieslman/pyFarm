from pydantic import BaseModel, Field


class FuelstationSlot(BaseModel):
    """Represents a production slot in the Biosprit-Anlage (Fuelstation)."""

    slot_id: int
    level: int = 1
    production_limit: int = 1_000_000
    current_points: int = 0
    level_points_left: int = 0
    remain: int = 0
    busy: bool = False
    is_blocked: bool = False
    accepted_products: dict[int, int] = Field(default_factory=dict)  # pid -> points_per_unit

    @property
    def points_needed(self) -> int:
        """Points required to reach production limit and start biofuel run."""
        return max(0, self.production_limit - self.current_points)

    @property
    def points_left(self) -> int:
        """Alias for points_needed for backwards compatibility."""
        return self.points_needed

    @points_left.setter
    def points_left(self, val: int):
        self.current_points = max(0, self.production_limit - val)

    @property
    def is_finished(self) -> bool:
        """Slot has a finished canister waiting to be harvested."""
        return self.busy and self.remain <= 0

    @property
    def is_waiting_for_refill(self) -> bool:
        """Slot is unlocked and ready to receive crops to start production."""
        return not self.busy and not self.is_blocked and self.points_needed > 0 and self.remain <= 0



class FuelstationState(BaseModel):
    """Overall state of the Biosprit-Anlage."""

    farm_id: int
    position: int
    level: int = 1
    tokens: int = 0
    slots: dict[int, FuelstationSlot] = Field(default_factory=dict)
