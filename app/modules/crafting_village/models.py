from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class WorkerSkill(BaseModel):
    """Experience and level for a specific quarry material."""

    points: int = 0
    level: int = 1
    percent: float = 0.0


class Worker(BaseModel):
    """Worker in the quarry (Steinbruch)."""

    id: str
    name: str = "Arbeiter"
    position: str = "1"
    energy: int = 100
    productionid: str | None = None
    data: dict[str, WorkerSkill] = Field(default_factory=dict)
    createdate: str | None = None

    @property
    def is_working(self) -> bool:
        """Worker is currently engaged in a mining production."""
        return bool(self.productionid and str(self.productionid) != "0")

    @property
    def energy_percent(self) -> float:
        """Worker energy percentage (0 to 100)."""
        return min(100.0, max(0.0, float(self.energy)))


class MineItem(BaseModel):
    """Equipment/Tool instance in the quarry inventory."""

    id: str
    name: str  # pickaxe, shoes, helmet, etc.
    durability: int = 20
    stock: int = 1  # 1 = available in backpack, 0 = equipped/in use

    @property
    def is_in_use(self) -> bool:
        return self.stock == 0

    @property
    def is_available(self) -> bool:
        return self.stock > 0 and self.durability > 0


class MineProductionSlot(BaseModel):
    """Active mining operation in a slot."""

    id: str
    slot: str
    position: str = "1"
    name: str  # sandstone, granite
    amount: int = 0
    workers: dict[str, int] = Field(default_factory=dict)
    items: dict[str, int] = Field(default_factory=dict)
    duration: int = 0
    remain: int = 0
    createdate: str | None = None
    finishdate: str | None = None

    @property
    def is_ready(self) -> bool:
        return self.remain <= 0


class MineMaterialConfig(BaseModel):
    """Configuration for mineable material (duration, energy cost, yields)."""

    name: str
    duration: int = 18000
    harvest: int = 5
    energy: int = 10
    worker: int = 1
    items: int = 2
    reward: dict[str, Any] = Field(default_factory=dict)


class MineShopItemConfig(BaseModel):
    """Tool catalogue specification in the mine store."""

    name: str
    money: int = 0
    coins: int = 0
    durability: int = 20
    bonus: dict[str, float] = Field(default_factory=dict)


class MineSnapshot(BaseModel):
    """Complete runtime state of the Steinbruch (Mine)."""

    level: int = 1
    stock: dict[str, int] = Field(default_factory=dict)
    workers: dict[str, Worker] = Field(default_factory=dict)
    items: dict[str, MineItem] = Field(default_factory=dict)
    productions: dict[str, MineProductionSlot] = Field(default_factory=dict)
    stats: dict[str, str] = Field(default_factory=dict)
    validenergyproducts: dict[str, int] = Field(default_factory=dict)
    materials_config: dict[str, MineMaterialConfig] = Field(default_factory=dict)
    shop_items_config: dict[str, MineShopItemConfig] = Field(default_factory=dict)
    last_updated: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


# =====================================================================
# Steinmetz (Stonemason) Models
# =====================================================================


class StonemasonToolConfig(BaseModel):
    """Tool catalogue item for stonemason carving (Meißel etc.)."""

    id: str
    name: str = "Meißel"
    x: int = 1
    y: int = 1
    duration: int = 1800
    money: dict[str, int] = Field(default_factory=dict)
    coins: dict[str, int] = Field(default_factory=dict)
    form: str | None = None

    @property
    def costs_coins(self) -> bool:
        """Returns True if the tool requires coins to buy."""
        return bool(self.coins)


class StonemasonProductConfig(BaseModel):
    """Recipe / sculpture pattern produced by the stonemason."""

    id: str
    name: str
    material: dict[str, int] = Field(default_factory=dict)
    level: int = 1
    x: int = 4
    y: int = 4
    allowed: list[int] = Field(default_factory=list)
    reward: dict[str, int] = Field(default_factory=dict)


class StonemasonCustomer(BaseModel):
    """Customer visiting the stonemason with a sculpture request."""

    id: str
    position: str = "1"
    name: str = "Kunde"
    pid: str = "1"
    slot: str = "0"  # "0" = in queue, "1".."4" = on workbench slot
    grid: dict[str, Any] | list[Any] | None = None  # tile_id -> {remain: int, ...}
    reward_multi: dict[str, float] = Field(default_factory=dict)
    remain: int = 0
    createdate: str = ""
    finishdate: str = ""

    @property
    def is_assigned(self) -> bool:
        """Customer is assigned to a workbench slot."""
        return self.slot not in ("0", 0, None)

    @property
    def is_waiting(self) -> bool:
        """Customer is waiting in the queue."""
        return not self.is_assigned

    @property
    def tiles(self) -> dict[str, Any]:
        """Return dictionary mapping of tiles in the grid."""
        if isinstance(self.grid, dict):
            return self.grid
        return {}

    def calculate_progress_percent(self, allowed_tiles_count: int) -> float:
        """Calculate chiseling progress percentage."""
        if not allowed_tiles_count or not self.tiles:
            return 0.0
        finished_tiles = len([k for k in self.tiles if str(k).isdigit()])
        return min(100.0, round((finished_tiles * 100.0) / allowed_tiles_count, 1))

    def calculate_rewards(
        self, product: StonemasonProductConfig | None
    ) -> dict[str, float]:
        """Calculate expected rewards (money, points, stonemason_points) including customer multipliers."""
        if not product or not product.reward:
            return {"money": 0.0, "points": 0.0, "stonemason_points": 0.0}

        multi = self.reward_multi
        base = product.reward
        return {
            "money": round(float(base.get("money", 0)) * float(multi.get("money", 1.0)), 2),
            "points": round(float(base.get("points", 0)) * float(multi.get("points", 1.0)), 2),
            "stonemason_points": round(
                float(base.get("stonemason_points", 0)) * float(multi.get("stonemason_points", 1.0)), 2
            ),
        }

    def get_reward_score(
        self, product: StonemasonProductConfig | None, priority: str = "overall"
    ) -> float:
        """Calculate a scalar score representing the value of the customer's reward."""
        rew = self.calculate_rewards(product)
        m = rew["money"]
        p = rew["points"]
        sm = rew["stonemason_points"]

        if priority == "stonemason_points":
            return sm * 1000.0 + m * 0.1 + p * 0.01
        elif priority == "money":
            return m + sm * 10.0 + p * 0.01
        elif priority == "points":
            return p + sm * 100.0 + m * 0.1
        else:  # "overall"
            # Balanced: 1 Steinmetz-Punkt is weighted as ~100 kT, points as ~0.1 kT
            return sm * 100.0 + m + p * 0.1


class StonemasonSlotConfig(BaseModel):
    """Configuration for a stonemason workbench slot."""

    level: int = 1


class StonemasonSnapshot(BaseModel):
    """Complete runtime state of the Steinmetz (Stonemason)."""

    id: str | None = None
    level: int = 1
    points: int = 0
    level_percent: float = 0.0
    level_rest: int = 100
    count: int = 0
    count_alltime: int = 0
    tools: dict[str, int] = Field(default_factory=dict)  # tool_id -> quantity
    customers: dict[str, StonemasonCustomer] = Field(default_factory=dict)
    slots_config: dict[str, StonemasonSlotConfig] = Field(default_factory=dict)
    tools_config: dict[str, StonemasonToolConfig] = Field(default_factory=dict)
    products_config: dict[str, StonemasonProductConfig] = Field(default_factory=dict)
    stats: dict[str, str] = Field(default_factory=dict)
    last_updated: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def is_working(self) -> bool:
        """Returns True if any tile across any active slot is currently being chiseled (remain > 0)."""
        for cust in self.customers.values():
            if cust.is_assigned:
                for tile_data in cust.tiles.values():
                    if isinstance(tile_data, dict) and tile_data.get("remain", 0) > 0:
                        return True
        return False

    def get_customer_on_slot(self, slot: int | str) -> StonemasonCustomer | None:
        """Get customer assigned to the given slot number."""
        slot_str = str(slot)
        for cust in self.customers.values():
            if cust.slot == slot_str:
                return cust
        return None

    def get_waiting_customers(self) -> list[StonemasonCustomer]:
        """Get all customers waiting in queue."""
        return [c for c in self.customers.values() if c.is_waiting]

    @property
    def active_slots_count(self) -> int:
        """Number of customers currently assigned to a slot."""
        return sum(1 for c in self.customers.values() if c.is_assigned)

    @property
    def waiting_customers_count(self) -> int:
        """Number of customers waiting in line."""
        return sum(1 for c in self.customers.values() if c.is_waiting)


class CraftingVillageSnapshot(BaseModel):
    """Overall state of the Handwerkerdorf (Mine, Stonemason, Quests)."""

    mine: MineSnapshot | None = None
    stonemason: StonemasonSnapshot | None = None
    quests: list[dict[str, Any]] = Field(default_factory=list)
    last_updated: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class CraftingVillageSummary(BaseModel):
    """Compact summary for dashboard cards and status reports."""

    active: bool = False
    mine_level: int = 1
    workers_count: int = 0
    working_workers_count: int = 0
    active_productions_count: int = 0
    ready_productions_count: int = 0
    sandstone_stock: int = 0
    granite_stock: int = 0
    stonemason_level: int = 0
    stonemason_points: int = 0
    stonemason_customers_count: int = 0
    stonemason_active_slots_count: int = 0
    last_updated: str | None = None
