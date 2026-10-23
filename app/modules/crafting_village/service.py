from datetime import UTC, datetime

from loguru import logger

from app.core.client import MFFGameClient
from app.modules.crafting_village.models import (
    CraftingVillageSnapshot,
    CraftingVillageSummary,
    MineItem,
    MineMaterialConfig,
    MineProductionSlot,
    MineShopItemConfig,
    MineSnapshot,
    Worker,
    WorkerSkill,
)


class CraftingVillageService:
    """Manages Handwerkerdorf operations (Steinbruch / Mine, Quests, upcoming Steinmetz)."""

    def __init__(self, client: MFFGameClient):
        self.client = client
        self.snapshot: CraftingVillageSnapshot | None = None

    async def init_remote(self) -> CraftingVillageSnapshot | None:
        """Fetch quarry and crafting village state from upstream game server."""
        try:
            res = await self.client.api_call("farm", {"mode": "mine_init"})
            datablock = res.get("datablock", {})
            if not datablock or "data" not in datablock:
                logger.debug("CraftingVillageService: Keine Handwerkerdorf-/Mine-Daten empfangen.")
                return None

            data = datablock.get("data", {})
            config = datablock.get("config", {})

            # 1. Parse Workers
            workers_map: dict[str, Worker] = {}
            raw_workers = data.get("workers", {})
            if isinstance(raw_workers, dict):
                for wid, w_info in raw_workers.items():
                    if not isinstance(w_info, dict):
                        continue
                    # Parse skill data per material
                    skills: dict[str, WorkerSkill] = {}
                    for mat_name, s_data in w_info.get("data", {}).items():
                        if isinstance(s_data, dict):
                            skills[mat_name] = WorkerSkill(
                                points=int(s_data.get("points", 0)),
                                level=int(s_data.get("level", 1)),
                                percent=float(s_data.get("percent", 0.0)),
                            )

                    workers_map[str(wid)] = Worker(
                        id=str(w_info.get("id", wid)),
                        name=str(w_info.get("name", "Arbeiter")),
                        position=str(w_info.get("position", "1")),
                        energy=int(w_info.get("energy", 100)),
                        productionid=str(w_info.get("productionid"))
                        if w_info.get("productionid")
                        else None,
                        data=skills,
                        createdate=str(w_info.get("createdate", "")),
                    )

            # 2. Parse Items (Tools)
            items_map: dict[str, MineItem] = {}
            raw_items = data.get("items", {})
            if isinstance(raw_items, dict):
                for iid, i_info in raw_items.items():
                    if not isinstance(i_info, dict):
                        continue
                    items_map[str(iid)] = MineItem(
                        id=str(i_info.get("id", iid)),
                        name=str(i_info.get("name", "unknown")),
                        durability=int(i_info.get("durability", 20)),
                        stock=int(i_info.get("stock", 1)),
                    )

            # 3. Parse Productions (Running mining slots)
            productions_map: dict[str, MineProductionSlot] = {}
            raw_prods = data.get("productions", {})
            if isinstance(raw_prods, dict):
                for slot_key, sub_prods in raw_prods.items():
                    if isinstance(sub_prods, dict):
                        for sub_key, p_info in sub_prods.items():
                            if isinstance(p_info, dict):
                                p_id = str(p_info.get("id", f"{slot_key}_{sub_key}"))
                                productions_map[str(slot_key)] = MineProductionSlot(
                                    id=p_id,
                                    slot=str(p_info.get("slot", slot_key)),
                                    position=str(p_info.get("position", "1")),
                                    name=str(p_info.get("name", "sandstone")),
                                    amount=int(p_info.get("amount", 0)),
                                    workers={
                                        str(k): int(v) for k, v in p_info.get("workers", {}).items()
                                    },
                                    items={
                                        str(k): int(v) for k, v in p_info.get("items", {}).items()
                                    },
                                    duration=int(p_info.get("duration", 0)),
                                    remain=int(p_info.get("remain", 0)),
                                    createdate=str(p_info.get("createdate", "")),
                                    finishdate=str(p_info.get("finishdate", "")),
                                )

            # 4. Parse Material Config
            materials_map: dict[str, MineMaterialConfig] = {}
            for mat_key, m_info in config.get("material", {}).items():
                if isinstance(m_info, dict):
                    materials_map[mat_key] = MineMaterialConfig(
                        name=str(m_info.get("name", mat_key)),
                        duration=int(m_info.get("duration", 18000)),
                        harvest=int(m_info.get("harvest", 5)),
                        energy=int(m_info.get("energy", 10)),
                        worker=int(m_info.get("worker", 1)),
                        items=int(m_info.get("items", 2)),
                        reward=m_info.get("reward", {}),
                    )

            # 5. Parse Shop Items Config
            shop_items_map: dict[str, MineShopItemConfig] = {}
            for item_key, s_info in config.get("items", {}).items():
                if isinstance(s_info, dict):
                    shop_items_map[item_key] = MineShopItemConfig(
                        name=str(s_info.get("name", item_key)),
                        money=int(s_info.get("money", 0)),
                        coins=int(s_info.get("coins", 0)),
                        durability=int(s_info.get("durability", 20)),
                        bonus={k: float(v) for k, v in s_info.get("bonus", {}).items()},
                    )

            # 6. Parse Stock & Energy Products
            raw_stock = data.get("stock", {})
            stock_dict = (
                {k: int(v) for k, v in raw_stock.items()} if isinstance(raw_stock, dict) else {}
            )
            energy_prods = (
                {str(k): int(v) for k, v in data.get("validenergyproducts", {}).items()}
                if isinstance(data.get("validenergyproducts"), dict)
                else {}
            )

            stats = (
                {str(k): str(v) for k, v in data.get("stats", {}).items()}
                if isinstance(data.get("stats"), dict)
                else {}
            )

            mine_snap = MineSnapshot(
                level=int(data.get("level", 1)),
                stock=stock_dict,
                workers=workers_map,
                items=items_map,
                productions=productions_map,
                stats=stats,
                validenergyproducts=energy_prods,
                materials_config=materials_map,
                shop_items_config=shop_items_map,
                last_updated=datetime.now(UTC).isoformat(),
            )

            self.snapshot = CraftingVillageSnapshot(
                mine=mine_snap,
                stonemason=None,
                quests=[],
                last_updated=datetime.now(UTC).isoformat(),
            )
            return self.snapshot

        except Exception as e:
            logger.error(f"CraftingVillageService: Fehler bei mine_init: {e}")
            return None

    def get_summary(self) -> CraftingVillageSummary:
        """Provide a high-level summary for the dashboard and API."""
        if not self.snapshot or not self.snapshot.mine:
            return CraftingVillageSummary(active=False)

        mine = self.snapshot.mine
        workers_count = len(mine.workers)
        working_count = sum(1 for w in mine.workers.values() if w.is_working)
        active_prod_count = len(mine.productions)
        ready_prod_count = sum(1 for p in mine.productions.values() if p.is_ready)

        return CraftingVillageSummary(
            active=True,
            mine_level=mine.level,
            workers_count=workers_count,
            working_workers_count=working_count,
            active_productions_count=active_prod_count,
            ready_productions_count=ready_prod_count,
            sandstone_stock=mine.stock.get("sandstone", 0),
            granite_stock=mine.stock.get("granite", 0),
            last_updated=mine.last_updated,
        )
