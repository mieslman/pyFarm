from typing import Any
from pydantic import BaseModel, Field


# Direct mapping: raw spice (spice) -> dried spice (spicedried)
SPICE_RAW_TO_DRIED: dict[int, int] = {
    1100: 1110,  # Pfeffer -> Pfeffer getrocknet
    1101: 1111,  # Zimt -> Zimt getrocknet
    1102: 1112,  # Muskat -> Muskat getrocknet
    1103: 1113,  # Kardamom -> Kardamom getrocknet
    1104: 1114,  # Nelke -> Nelke getrocknet
    1105: 1115,  # Piment -> Piment getrocknet
    1106: 1116,  # Sternanis -> Sternanis getrocknet
    113: 1107,   # Chili -> Chili getrocknet
    701: 1108,   # Salbei -> Salbei getrocknet
    703: 1109,   # Kümmel -> Kümmel getrocknet
}

# Reverse mapping: dried spice -> raw spice
SPICE_DRIED_TO_RAW: dict[int, int] = {v: k for k, v in SPICE_RAW_TO_DRIED.items()}

# Direct mapping: dried spice (spicedried) -> ground spice (spiceground)
SPICE_DRIED_TO_GROUND: dict[int, int] = {
    1110: 1120,  # Pfeffer getrocknet -> Pfeffer gemahlen
    1111: 1121,  # Zimt getrocknet -> Zimt gemahlen
    1112: 1122,  # Muskat getrocknet -> Muskat gemahlen
    1113: 1123,  # Kardamom getrocknet -> Kardamom gemahlen
    1114: 1124,  # Nelke getrocknet -> Nelke gemahlen
    1115: 1125,  # Piment getrocknet -> Piment gemahlen
    1116: 1126,  # Sternanis getrocknet -> Sternanis gemahlen
    1107: 1117,  # Chili getrocknet -> Chili gemahlen
    1108: 1118,  # Salbei getrocknet -> Salbei gemahlen
    1109: 1119,  # Kümmel getrocknet -> Kümmel gemahlen
}

# Reverse mapping: ground spice -> dried spice
SPICE_GROUND_TO_DRIED: dict[int, int] = {v: k for k, v in SPICE_DRIED_TO_GROUND.items()}

# Milling duration in seconds per unit for dried spices in the spice mill
SPICE_MILL_DURATIONS: dict[int, int] = {
    1107: 600,  # Chili getrocknet (10 min)
    1108: 720,  # Salbei getrocknet (12 min)
    1109: 720,  # Kümmel getrocknet (12 min)
    1110: 120,  # Pfeffer getrocknet (2 min)
    1111: 240,  # Zimt getrocknet (4 min)
    1112: 300,  # Muskat getrocknet (5 min)
    1113: 360,  # Kardamom getrocknet (6 min)
    1114: 420,  # Nelke getrocknet (7 min)
    1115: 600,  # Piment getrocknet (10 min)
    1116: 600,  # Sternanis getrocknet (10 min)
}


class SpicehouseConfig(BaseModel):
    """Configuration settings for automated Gewürzhaus management."""
    enabled: bool = Field(default=True, description="Gewürzhaus-Automatisierung aktiv")
    auto_oven: bool = Field(default=True, description="Trockenofen automatisch leeren und befüllen")
    auto_mill: bool = Field(default=True, description="Gewürzmühlen automatisch ernten und bestücken")
    auto_customer: bool = Field(default=True, description="Kunden (Farmis) bei Deckung automatisch bedienen")
    min_crop_reserve: int = Field(default=500, description="Sicherheitsreserve auf Farm 10 für Acker-Gewürze")


class OvenSlotInfo(BaseModel):
    """Represents one oven line (Blech) in the drying oven."""
    line: int
    level: int = 1
    capacity: int = 10
    slots: int = 4
    is_rented: bool = False
    remain: int = 0  # Rental expiration or line-specific remain if rented

    @property
    def is_available(self) -> bool:
        """Determines if the line is available for baking (free line 1..3 or active rental)."""
        if self.line in (1, 2, 3):
            return True
        return self.is_rented and self.remain > 0


class MillSlotInfo(BaseModel):
    """Represents one spice mill in the Gewürzhaus."""
    slot: int
    level: int = 1
    capacity: int = 10
    pid: int | None = None
    name: str = ""
    amount: int = 0
    amount_original: int = 0
    output: int = 0
    remain: int = 0
    duration: int = 0
    start: int = 0
    is_rented: bool = False

    @property
    def is_available(self) -> bool:
        """Determines if the mill is available (free slot 1..3 or active rental)."""
        if self.slot in (1, 2, 3):
            return True
        return self.is_rented and self.remain > 0

    @property
    def is_idle(self) -> bool:
        """Mill is ready to accept new dried spice to grind."""
        if self.slot == 4 and (not self.is_rented or self.remain <= 0):
            return False
        return self.output == 0 and self.amount == 0

    @property
    def is_ready_to_harvest(self) -> bool:
        """Mill has finished output waiting to be collected."""
        return self.output > 0

    @property
    def is_ready(self) -> bool:
        """Alias for is_ready_to_harvest or finished milling."""
        return self.output > 0 or (self.pid is not None and self.remain <= 0 and (self.amount > 0 or self.amount_original > 0))


class SpiceCustomer(BaseModel):
    """Represents a customer visiting the Gewürzhaus."""
    id: str
    slot: int
    data: dict[int, int] = Field(default_factory=dict, description="{pid: amount}")
    reward_points: int = 0
    reward_money: float = 0.0
    reward_spicehouse_points: int = 0

    def is_satisfied(self, stock: dict[int, int]) -> bool:
        """Check if all demanded spice amounts are available in the given stock dictionary."""
        for pid, amt in self.data.items():
            if stock.get(pid, 0) < amt:
                return False
        return True


class SpicehouseState(BaseModel):
    """Overall snapshot of the Gewürzhaus state."""
    level: int = 1
    points: int = 0
    oven_remain: int = 0
    oven_is_ready: bool = False
    oven_is_idle: bool = True
    oven_lines: dict[int, OvenSlotInfo] = Field(default_factory=dict)
    mill_slots: dict[int, MillSlotInfo] = Field(default_factory=dict)
    customers: dict[int, SpiceCustomer] = Field(default_factory=dict)
