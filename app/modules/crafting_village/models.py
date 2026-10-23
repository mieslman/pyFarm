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


class CraftingVillageSnapshot(BaseModel):
    """Overall state of the Handwerkerdorf (Mine, upcoming Stonemason, Quests)."""

    mine: MineSnapshot | None = None
    stonemason: dict[str, Any] | None = None  # Placeholder for future Stonemason (Steinmetz)
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
    last_updated: str | None = None
