import json
import math
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from app.config import settings
from app.core.client import MFFGameClient
from app.modules.crafting_village.models import (
    CraftingVillageSnapshot,
    CraftingVillageSummary,
    MineItem,
    MineMaterialConfig,
    MineProductionSlot,
    MineShopItemConfig,
    MineSnapshot,
    StonemasonCustomer,
    StonemasonProductConfig,
    StonemasonSlotConfig,
    StonemasonSnapshot,
    StonemasonToolConfig,
    Worker,
    WorkerSkill,
)


class CraftingVillageService:
    """Manages Handwerkerdorf operations (Steinbruch / Mine, Quests, upcoming Steinmetz)."""

    def __init__(self, client: MFFGameClient):
        self.client = client
        self.snapshot: CraftingVillageSnapshot | None = None

    def _parse_mine_data(self, data: dict[str, Any], config: dict[str, Any]) -> MineSnapshot:
        """Hydrate MineSnapshot from raw datablock/updateblock dictionaries."""
        # 1. Parse Workers
        workers_map: dict[str, Worker] = {}
        raw_workers = data.get("workers") or {}
        if isinstance(raw_workers, dict):
            for wid, w_info in raw_workers.items():
                if not isinstance(w_info, dict):
                    continue
                skills: dict[str, WorkerSkill] = {}
                raw_w_data = w_info.get("data") or {}
                if isinstance(raw_w_data, dict):
                    for mat_name, s_data in raw_w_data.items():
                        if isinstance(s_data, dict):
                            skills[mat_name] = WorkerSkill(
                                points=int(s_data.get("points", 0)),
                                level=int(s_data.get("level", 1)),
                                percent=float(s_data.get("percent", 0.0)),
                            )

                raw_prod_id = w_info.get("productionid")
                prod_id_str = str(raw_prod_id) if raw_prod_id not in (None, "0", 0) else None

                workers_map[str(wid)] = Worker(
                    id=str(w_info.get("id", wid)),
                    name=str(w_info.get("name", "Arbeiter")),
                    position=str(w_info.get("position", "1")),
                    energy=int(w_info.get("energy", 100)),
                    productionid=prod_id_str,
                    data=skills,
                    createdate=str(w_info.get("createdate", "")),
                )

        # 2. Parse Items (Tools)
        items_map: dict[str, MineItem] = {}
        raw_items = data.get("items") or {}
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
        raw_prods = data.get("productions") or {}
        if isinstance(raw_prods, dict):
            for slot_key, sub_prods in raw_prods.items():
                if isinstance(sub_prods, dict):
                    for sub_key, p_info in sub_prods.items():
                        if isinstance(p_info, dict):
                            p_id = str(p_info.get("id", f"{slot_key}_{sub_key}"))
                            raw_p_workers = p_info.get("workers") or {}
                            raw_p_items = p_info.get("items") or {}
                            productions_map[str(slot_key)] = MineProductionSlot(
                                id=p_id,
                                slot=str(p_info.get("slot", slot_key)),
                                position=str(p_info.get("position", "1")),
                                name=str(p_info.get("name", "sandstone")),
                                amount=int(p_info.get("amount", 0)),
                                workers={
                                    str(k): int(v)
                                    for k, v in raw_p_workers.items()
                                    if isinstance(raw_p_workers, dict)
                                },
                                items={
                                    str(k): int(v)
                                    for k, v in raw_p_items.items()
                                    if isinstance(raw_p_items, dict)
                                },
                                duration=int(p_info.get("duration", 0)),
                                remain=int(p_info.get("remain", 0)),
                                createdate=str(p_info.get("createdate", "")),
                                finishdate=str(p_info.get("finishdate", "")),
                            )

        # 4. Parse Material Config
        materials_map: dict[str, MineMaterialConfig] = {}
        raw_mat = config.get("material") or {}
        if isinstance(raw_mat, dict):
            for mat_key, m_info in raw_mat.items():
                if isinstance(m_info, dict):
                    materials_map[mat_key] = MineMaterialConfig(
                        name=str(m_info.get("name", mat_key)),
                        duration=int(m_info.get("duration", 18000)),
                        harvest=int(m_info.get("harvest", 5)),
                        energy=int(m_info.get("energy", 10)),
                        worker=int(m_info.get("worker", 1)),
                        items=int(m_info.get("items", 2)),
                        reward=m_info.get("reward") or {},
                    )

        # 5. Parse Shop Items Config
        shop_items_map: dict[str, MineShopItemConfig] = {}
        raw_shop_items = config.get("items") or {}
        if isinstance(raw_shop_items, dict):
            for item_key, s_info in raw_shop_items.items():
                if isinstance(s_info, dict):
                    raw_bonus = s_info.get("bonus") or {}
                    bonus_dict = (
                        {k: float(v) for k, v in raw_bonus.items()}
                        if isinstance(raw_bonus, dict)
                        else {}
                    )
                    shop_items_map[item_key] = MineShopItemConfig(
                        name=str(s_info.get("name", item_key)),
                        money=int(s_info.get("money", 0)),
                        coins=int(s_info.get("coins", 0)),
                        durability=int(s_info.get("durability", 20)),
                        bonus=bonus_dict,
                    )

        # 6. Parse Stock & Energy Products
        raw_stock = data.get("stock") or {}
        stock_dict = (
            {k: int(v) for k, v in raw_stock.items()} if isinstance(raw_stock, dict) else {}
        )
        raw_energy = data.get("validenergyproducts") or {}
        energy_prods = (
            {str(k): int(v) for k, v in raw_energy.items()} if isinstance(raw_energy, dict) else {}
        )
        raw_stats = data.get("stats") or {}
        stats = {str(k): str(v) for k, v in raw_stats.items()} if isinstance(raw_stats, dict) else {}

        return MineSnapshot(
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

    def _parse_stonemason_data(
        self, data: dict[str, Any], config: dict[str, Any]
    ) -> StonemasonSnapshot:
        """Hydrate StonemasonSnapshot from raw stonemason datablock/config dictionaries."""
        # 1. Tools inventory
        raw_tools = data.get("tools") or {}
        tools_dict = (
            {str(k): int(v) for k, v in raw_tools.items()} if isinstance(raw_tools, dict) else {}
        )

        # 2. Customers
        customers_map: dict[str, StonemasonCustomer] = {}
        raw_custs = data.get("customers") or {}
        if isinstance(raw_custs, dict):
            for cid, c_info in raw_custs.items():
                if not isinstance(c_info, dict):
                    continue
                raw_grid = c_info.get("grid")
                raw_multi = c_info.get("reward_multi") or {}
                multi_dict = (
                    {str(k): float(v) for k, v in raw_multi.items()}
                    if isinstance(raw_multi, dict)
                    else {}
                )
                customers_map[str(cid)] = StonemasonCustomer(
                    id=str(c_info.get("id", cid)),
                    position=str(c_info.get("position", "1")),
                    name=str(c_info.get("name", "Kunde")),
                    pid=str(c_info.get("pid", "1")),
                    slot=str(c_info.get("slot", "0")),
                    grid=raw_grid if isinstance(raw_grid, dict) else None,
                    reward_multi=multi_dict,
                    remain=int(c_info.get("remain", 0)),
                    createdate=str(c_info.get("createdate", "")),
                    finishdate=str(c_info.get("finishdate", "")),
                )

        # 3. Slots Config
        slots_cfg_map: dict[str, StonemasonSlotConfig] = {}
        raw_slots = config.get("slots") or {}
        if isinstance(raw_slots, dict):
            for sid, s_info in raw_slots.items():
                if isinstance(s_info, dict):
                    slots_cfg_map[str(sid)] = StonemasonSlotConfig(
                        level=int(s_info.get("level", 1))
                    )

        # 4. Tools Config
        tools_cfg_map: dict[str, StonemasonToolConfig] = {}
        raw_tools_cfg = config.get("tools") or {}
        if isinstance(raw_tools_cfg, dict):
            for tid, t_info in raw_tools_cfg.items():
                if isinstance(t_info, dict):
                    raw_money = t_info.get("money") or {}
                    raw_coins = t_info.get("coins") or {}
                    tools_cfg_map[str(tid)] = StonemasonToolConfig(
                        id=str(tid),
                        name=str(t_info.get("name", f"Meißel {tid}")),
                        x=int(t_info.get("x", 1)),
                        y=int(t_info.get("y", 1)),
                        duration=int(t_info.get("duration", 1800)),
                        money=(
                            {str(k): int(v) for k, v in raw_money.items()}
                            if isinstance(raw_money, dict)
                            else {}
                        ),
                        coins=(
                            {str(k): int(v) for k, v in raw_coins.items()}
                            if isinstance(raw_coins, dict)
                            else {}
                        ),
                        form=str(t_info.get("form")) if t_info.get("form") else None,
                    )

        # 5. Products Config
        prods_cfg_map: dict[str, StonemasonProductConfig] = {}
        raw_prods_cfg = config.get("products") or {}
        if isinstance(raw_prods_cfg, dict):
            for pid, p_info in raw_prods_cfg.items():
                if isinstance(p_info, dict):
                    raw_mat = p_info.get("material") or {}
                    raw_allowed = p_info.get("allowed") or []
                    raw_reward = p_info.get("reward") or {}
                    prods_cfg_map[str(pid)] = StonemasonProductConfig(
                        id=str(pid),
                        name=str(p_info.get("name", f"Produkt {pid}")),
                        material=(
                            {str(k): int(v) for k, v in raw_mat.items()}
                            if isinstance(raw_mat, dict)
                            else {}
                        ),
                        level=int(p_info.get("level", 1)),
                        x=int(p_info.get("x", 4)),
                        y=int(p_info.get("y", 4)),
                        allowed=[int(x) for x in raw_allowed] if isinstance(raw_allowed, list) else [],
                        reward=(
                            {str(k): int(v) for k, v in raw_reward.items()}
                            if isinstance(raw_reward, dict)
                            else {}
                        ),
                    )

        # 6. Stats
        raw_stats = data.get("stats") or {}
        stats_dict = (
            {str(k): str(v) for k, v in raw_stats.items()} if isinstance(raw_stats, dict) else {}
        )

        return StonemasonSnapshot(
            id=str(data.get("id")) if data.get("id") else None,
            level=int(data.get("level", 1)),
            points=int(data.get("points", 0)),
            level_percent=float(data.get("level_percent", 0)),
            level_rest=int(data.get("level_rest", 100)),
            count=int(data.get("count", 0)),
            count_alltime=int(data.get("count_alltime", 0)),
            tools=tools_dict,
            customers=customers_map,
            slots_config=slots_cfg_map,
            tools_config=tools_cfg_map,
            products_config=prods_cfg_map,
            stats=stats_dict,
            last_updated=datetime.now(UTC).isoformat(),
        )

    async def init_remote(self) -> CraftingVillageSnapshot | None:
        """Fetch quarry and stonemason state from upstream game server."""
        mine_snap = None
        stonemason_snap = None

        # 1. Fetch Steinbruch (Mine)
        try:
            res = await self.client.api_call("farm", {"mode": "mine_init"})
            datablock = res.get("datablock", {})
            if datablock and "data" in datablock:
                mine_snap = self._parse_mine_data(
                    datablock.get("data") or {},
                    datablock.get("config") or {},
                )
        except Exception as e:
            logger.error(f"CraftingVillageService: Fehler bei mine_init: {e}")

        # 2. Fetch Steinmetz (Stonemason)
        try:
            sm_res = await self.client.api_call("farm", {"mode": "stonemason_init"})
            sm_datablock = sm_res.get("datablock", {})
            if sm_datablock and "data" in sm_datablock:
                stonemason_snap = self._parse_stonemason_data(
                    sm_datablock.get("data") or {},
                    sm_datablock.get("config") or {},
                )
        except Exception as e:
            logger.debug(f"CraftingVillageService: Steinmetz noch nicht freigeschaltet oder Fehler: {e}")

        if not mine_snap and not stonemason_snap:
            logger.debug("CraftingVillageService: Weder Mine- noch Steinmetz-Daten empfangen.")
            return None

        self.snapshot = CraftingVillageSnapshot(
            mine=mine_snap,
            stonemason=stonemason_snap,
            quests=[],
            last_updated=datetime.now(UTC).isoformat(),
        )
        return self.snapshot

    async def finish_production(
        self, slot: str | int = 1, position: str | int = 1
    ) -> dict[str, Any] | None:
        """Harvest completed mining production in the specified slot."""
        try:
            logger.info(f"CraftingVillageService: Ernte fertigen Abbau in Slot {slot}...")
            res = await self.client.api_call(
                "farm",
                {
                    "mode": "mine_finish_production",
                    "slot": int(slot),
                    "position": int(position),
                },
            )
            datablock = res.get("datablock", {})
            reward = datablock.get("reward", {}) if isinstance(datablock, dict) else {}
            materials = reward.get("materials", {})
            xp = reward.get("xp", {})
            money = reward.get("money", 0)
            points = reward.get("points", 0)
            logger.info(
                f"CraftingVillageService: Slot {slot} erfolgreich geerntet! "
                f"Ertrag: {materials}, XP: {xp}, Geld: +{money} kT, Punkte: +{points}"
            )

            # Re-hydrate state if updateblock provided
            ub = res.get("updateblock", {})
            if "mine" in ub and "data" in ub["mine"]:
                cfg = (
                    self.snapshot.mine.materials_config
                    if self.snapshot and self.snapshot.mine
                    else {}
                )
                self.snapshot.mine = self._parse_mine_data(
                    ub["mine"].get("data", {}), ub["mine"].get("config", cfg)
                )

            return reward
        except Exception as e:
            logger.error(f"CraftingVillageService: Fehler beim Ernten von Slot {slot}: {e}")
            return None

    async def refill_worker_energy(
        self,
        worker_id: str | int,
        pid: int | None = None,
        amount: int | None = None,
        stock_service: Any | None = None,
    ) -> bool:
        """Refill a worker's energy using valid farm products (e.g. Carrots PID 1)."""
        try:
            if not self.snapshot or not self.snapshot.mine:
                await self.init_remote()
                if not self.snapshot or not self.snapshot.mine:
                    return False

            mine = self.snapshot.mine
            worker = mine.workers.get(str(worker_id))
            if not worker:
                logger.warning(f"CraftingVillageService: Arbeiter ID {worker_id} nicht gefunden.")
                return False

            current_energy = int(worker.energy)
            max_energy = 100
            missing_energy = max(0, max_energy - current_energy)
            if missing_energy <= 0:
                logger.debug(
                    f"CraftingVillageService: Arbeiter {worker.name} hat bereits volle Energie (100)."
                )
                return True

            chosen_pid = pid
            chosen_amount = amount

            if chosen_pid is None or chosen_amount is None:
                energy_map = mine.validenergyproducts
                preferred_pids = [1, 2, 3, 4, 5]

                selected_candidate: int | None = None
                for cand in preferred_pids:
                    if str(cand) in energy_map or cand in energy_map:
                        if stock_service:
                            avail_stock = stock_service.get_stock(cand) - getattr(
                                settings.stock, "buffer_crops", 0
                            )
                            if avail_stock > 0:
                                selected_candidate = cand
                                break
                        else:
                            selected_candidate = cand
                            break

                if selected_candidate is None:
                    for k in energy_map:
                        selected_candidate = int(k)
                        break

                if selected_candidate is None:
                    logger.warning(
                        "CraftingVillageService: Keine validen Energieprodukte in Mine verfügbar."
                    )
                    return False

                chosen_pid = selected_candidate
                energy_per_unit = energy_map.get(str(chosen_pid), energy_map.get(chosen_pid, 1))
                if energy_per_unit <= 0:
                    energy_per_unit = 1

                needed_units = math.ceil(missing_energy / energy_per_unit)
                if stock_service:
                    avail_stock = max(
                        0,
                        stock_service.get_stock(chosen_pid)
                        - getattr(settings.stock, "buffer_crops", 0),
                    )
                    chosen_amount = min(needed_units, avail_stock)
                    if chosen_amount <= 0:
                        logger.warning(
                            f"CraftingVillageService: Zu wenig Bestand von Produkt {chosen_pid} für Energie-Refill von {worker.name}."
                        )
                        return False
                else:
                    chosen_amount = needed_units

            logger.info(
                f"CraftingVillageService: Fülle Energie von Arbeiter {worker.name} (ID {worker_id}) "
                f"auf: {chosen_amount}x PID {chosen_pid} (aktuelle Energie: {current_energy}/100)..."
            )

            res = await self.client.api_call(
                "farm",
                {
                    "mode": "mine_worker_refill",
                    "id": int(worker_id),
                    "pid": int(chosen_pid),
                    "amount": int(chosen_amount),
                },
            )

            if stock_service and "updateblock" in res:
                stock_service.update(res)

            ub = res.get("updateblock", {})
            if "mine" in ub and "data" in ub["mine"]:
                cfg = (
                    self.snapshot.mine.materials_config
                    if self.snapshot and self.snapshot.mine
                    else {}
                )
                self.snapshot.mine = self._parse_mine_data(
                    ub["mine"].get("data", {}), ub["mine"].get("config", cfg)
                )

            updated_worker = (
                self.snapshot.mine.workers.get(str(worker_id))
                if self.snapshot and self.snapshot.mine
                else None
            )
            new_energy = updated_worker.energy if updated_worker else "unbekannt"
            logger.info(
                f"CraftingVillageService: Arbeiter {worker.name} erfolgreich aufgeladen! "
                f"Neue Energie: {new_energy}/100."
            )
            return True

        except Exception as e:
            logger.error(
                f"CraftingVillageService: Fehler beim Aufladen von Arbeiter {worker_id}: {e}"
            )
            return False

    def get_best_buyable_tool(self) -> tuple[str, MineShopItemConfig] | None:
        """Find the shop tool with highest XP bonus that can be bought with money/kT (no coins)."""
        if not self.snapshot or not self.snapshot.mine:
            return None

        mine = self.snapshot.mine
        kt_tools: list[tuple[str, MineShopItemConfig]] = []

        for name, item_cfg in mine.shop_items_config.items():
            # Strict Coin-Schutz: Keine Werkzeuge kaufen, die Coins kosten!
            if item_cfg.coins > 0:
                continue
            if item_cfg.money <= 0:
                continue
            kt_tools.append((name, item_cfg))

        if not kt_tools:
            return None

        def tool_sort_key(entry: tuple[str, MineShopItemConfig]) -> tuple[float, int]:
            _, cfg = entry
            return (cfg.bonus.get("xp", 0.0), cfg.durability)

        # Sort descending by XP bonus
        kt_tools.sort(key=tool_sort_key, reverse=True)
        return kt_tools[0]

    async def buy_shop_item(
        self,
        name: str = "shoes",
        amount: int = 1,
        stock_service: Any | None = None,
    ) -> bool:
        """Buy equipment tool in quarry shop using kT (strictly protected against coin costs)."""
        try:
            if not self.snapshot or not self.snapshot.mine:
                await self.init_remote()
                if not self.snapshot or not self.snapshot.mine:
                    return False

            mine = self.snapshot.mine
            item_cfg = mine.shop_items_config.get(name)
            if not item_cfg:
                logger.warning(
                    f"CraftingVillageService: Werkzeug '{name}' nicht im Shop-Katalog gefunden."
                )
                return False

            # Coin-Protection: Keine Werkzeuge mit Coins kaufen!
            if item_cfg.coins > 0:
                logger.warning(
                    f"CraftingVillageService: Kauf von '{name}' abgelehnt, da er {item_cfg.coins} Coins kosten würde!"
                )
                return False

            total_cost_kt = item_cfg.money * amount
            if stock_service:
                credits_kt = getattr(stock_service, "credits_kt", 0.0)
                min_credit = getattr(settings.trade, "min_credit_kt", 500.0)
                if credits_kt - total_cost_kt < min_credit:
                    logger.warning(
                        f"CraftingVillageService: Nicht genügend Guthaben ({credits_kt:.2f} kT) "
                        f"für Kauf von {amount}x '{name}' ({total_cost_kt} kT, Mindestreserve {min_credit} kT)."
                    )
                    return False

            logger.info(
                f"CraftingVillageService: Kaufe im Werkzeug-Shop {amount}x '{item_cfg.name}' ({name}) "
                f"für insgesamt {total_cost_kt} kT (XP-Bonus: +{item_cfg.bonus.get('xp', 0.0)})..."
            )

            res = await self.client.api_call(
                "farm",
                {
                    "mode": "mine_buy_shop_item",
                    "name": name,
                    "amount": int(amount),
                },
            )

            if stock_service and "updateblock" in res:
                stock_service.update(res)

            ub = res.get("updateblock", {})
            if "mine" in ub and "data" in ub["mine"]:
                cfg = (
                    self.snapshot.mine.materials_config
                    if self.snapshot and self.snapshot.mine
                    else {}
                )
                self.snapshot.mine = self._parse_mine_data(
                    ub["mine"].get("data", {}), ub["mine"].get("config", cfg)
                )

            logger.info(
                f"CraftingVillageService: Werkzeug '{item_cfg.name}' x{amount} erfolgreich gekauft!"
            )
            return True

        except Exception as e:
            logger.error(f"CraftingVillageService: Fehler beim Kauf von Werkzeug '{name}': {e}")
            return False

    def select_next_material(self) -> str:
        """Determine next material to mine: balance granite and sandstone based on lowest stock."""
        if not self.snapshot or not self.snapshot.mine:
            return "sandstone"

        pref = settings.crafting_village.preferred_material
        if pref in ("sandstone", "granite"):
            return pref

        # Default strategy: 'least_stock'
        sandstone_stock = self.snapshot.mine.stock.get("sandstone", 0)
        granite_stock = self.snapshot.mine.stock.get("granite", 0)

        if granite_stock < sandstone_stock:
            return "granite"
        elif sandstone_stock < granite_stock:
            return "sandstone"
        else:
            return "sandstone"

    def get_best_tools_for_mining(self, count: int = 2) -> list[MineItem]:
        """Select tools with highest experience bonus and sufficient durability."""
        if not self.snapshot or not self.snapshot.mine:
            return []

        mine = self.snapshot.mine
        available = [item for item in mine.items.values() if item.stock > 0 and item.durability > 0]

        def tool_rank(item: MineItem) -> tuple[float, int]:
            cfg = mine.shop_items_config.get(item.name)
            xp_bonus = cfg.bonus.get("xp", 0.0) if cfg else 0.0
            return (xp_bonus, item.durability)

        # Sort descending by XP bonus, then durability
        sorted_tools = sorted(available, key=tool_rank, reverse=True)
        return sorted_tools[:count]

    def get_best_worker_for_material(self, material: str) -> Worker | None:
        """Select idle worker with the highest skill level for this material and sufficient energy."""
        if not self.snapshot or not self.snapshot.mine:
            return None

        mine = self.snapshot.mine
        mat_cfg = mine.materials_config.get(material)
        required_energy = mat_cfg.energy if mat_cfg else 10

        idle_workers = [
            w for w in mine.workers.values() if not w.is_working and w.energy >= required_energy
        ]

        if not idle_workers:
            return None

        def worker_rank(w: Worker) -> tuple[int, int]:
            skill = w.data.get(material, WorkerSkill())
            return (skill.level, skill.points)

        sorted_workers = sorted(idle_workers, key=worker_rank, reverse=True)
        return sorted_workers[0]

    async def start_mining(
        self,
        slot: int = 1,
        position: int = 1,
        material: str = "sandstone",
        worker_id: str | int | None = None,
        item_ids: list[str | int] | None = None,
    ) -> bool:
        """Start a new mining operation with worker and tools."""
        try:
            setup = {
                "slot": int(slot),
                "position": int(position),
                "name": material,
                "workers": {"1": int(worker_id)},
                "items": {str(i + 1): int(iid) for i, iid in enumerate(item_ids)},
            }
            logger.info(
                f"CraftingVillageService: Starte Abbau in Slot {slot} ({material.upper()}) "
                f"mit Arbeiter ID {worker_id} und Werkzeugen {item_ids}..."
            )

            res = await self.client.api_call(
                "farm",
                {
                    "mode": "mine_start_production",
                    "setup": json.dumps(setup),
                },
            )

            # Re-hydrate if updateblock provided
            ub = res.get("updateblock", {})
            if "mine" in ub and "data" in ub["mine"]:
                cfg = (
                    self.snapshot.mine.materials_config
                    if self.snapshot and self.snapshot.mine
                    else {}
                )
                self.snapshot.mine = self._parse_mine_data(
                    ub["mine"].get("data", {}), ub["mine"].get("config", cfg)
                )

            logger.info(
                f"CraftingVillageService: Abbau in Slot {slot} ({material.upper()}) erfolgreich gestartet!"
            )
            return True
        except Exception as e:
            logger.error(
                f"CraftingVillageService: Fehler beim Starten des Abbaus in Slot {slot}: {e}"
            )
            return False

    def _update_from_updateblock(
        self, ub: dict[str, Any], stock_service: Any | None = None
    ) -> None:
        """Update mine and stonemason snapshot from game response updateblock."""
        if not isinstance(ub, dict):
            return

        if self.snapshot is None:
            self.snapshot = CraftingVillageSnapshot()

        if stock_service and "stock" in ub and ub["stock"] is not None:
            stock_service.update({"updateblock": ub})

        if "mine" in ub and isinstance(ub["mine"], dict) and "data" in ub["mine"]:
            raw_data = ub["mine"]["data"]
            cfg = (
                self.snapshot.mine.materials_config
                if self.snapshot and self.snapshot.mine
                else {}
            )
            shop_cfg = (
                self.snapshot.mine.shop_items_config
                if self.snapshot and self.snapshot.mine
                else {}
            )
            raw_cfg = ub["mine"].get("config") or {"material": cfg, "items": shop_cfg}
            if self.snapshot:
                self.snapshot.mine = self._parse_mine_data(raw_data, raw_cfg)

        if "stonemason" in ub and isinstance(ub["stonemason"], dict) and "data" in ub["stonemason"]:
            raw_sm_data = ub["stonemason"]["data"]
            sm_cfg = ub["stonemason"].get("config") or (
                {
                    "tools": {k: v.model_dump() for k, v in self.snapshot.stonemason.tools_config.items()},
                    "products": {k: v.model_dump() for k, v in self.snapshot.stonemason.products_config.items()},
                    "slots": {k: v.model_dump() for k, v in self.snapshot.stonemason.slots_config.items()},
                }
                if self.snapshot and self.snapshot.stonemason
                else {}
            )
            if self.snapshot:
                self.snapshot.stonemason = self._parse_stonemason_data(raw_sm_data, sm_cfg)

    async def assign_customer_to_slot(
        self, customer_id: str | int, slot: int, stock_service: Any | None = None
    ) -> bool:
        """Assign a waiting customer to an unlocked workbench slot (stonemason_set_slot)."""
        cid = str(customer_id)
        logger.info(f"CraftingVillage (Steinmetz): Weise Kunde {cid} Werkbank-Slot {slot} zu...")
        try:
            res = await self.client.api_call(
                "farm",
                {
                    "mode": "stonemason_set_slot",
                    "customerid": cid,
                    "slot": int(slot),
                },
            )
            if not res or res.get("datablock") != 1:
                logger.warning(
                    f"CraftingVillage (Steinmetz): Kunde {cid} konnte Slot {slot} nicht zugewiesen werden. Response: {res}"
                )
                return False

            ub = res.get("updateblock", {})
            self._update_from_updateblock(ub, stock_service=stock_service)
            logger.info(f"CraftingVillage (Steinmetz): Kunde {cid} erfolgreich Slot {slot} zugewiesen.")
            return True
        except Exception as e:
            logger.error(
                f"CraftingVillage (Steinmetz): Fehler bei Zuweisung von Kunde {cid} zu Slot {slot}: {e}"
            )
            return False

    async def chisel_tile(
        self,
        customer_id: str | int,
        tile: int,
        tool_id: int = 1,
        stock_service: Any | None = None,
    ) -> bool:
        """Start chiseling a tile on the sculpture grid (stonemason_click_tile)."""
        cid = str(customer_id)
        # Coin protection check
        if settings.crafting_village.coin_protection:
            if self.snapshot and self.snapshot.stonemason:
                tool_cfg = self.snapshot.stonemason.tools_config.get(str(tool_id))
                if tool_cfg and tool_cfg.costs_coins:
                    logger.warning(
                        f"CraftingVillage (Steinmetz): Coin-Schutz verhindert Nutzung von Werkzeug {tool_id} (kostet Coins!)."
                    )
                    return False

        logger.info(
            f"CraftingVillage (Steinmetz): Bearbeite Kachel {tile} von Kunde {cid} mit Werkzeug {tool_id}..."
        )
        try:
            res = await self.client.api_call(
                "farm",
                {
                    "mode": "stonemason_click_tile",
                    "customerid": cid,
                    "tile": int(tile),
                    "tool": int(tool_id),
                },
            )
            if not res or res.get("datablock") != 1:
                logger.warning(
                    f"CraftingVillage (Steinmetz): Kachel {tile} konnte nicht bearbeitet werden. Response: {res}"
                )
                return False

            ub = res.get("updateblock", {})
            self._update_from_updateblock(ub, stock_service=stock_service)
            logger.info(f"CraftingVillage (Steinmetz): Kachel {tile} erfolgreich gestartet.")
            return True
        except Exception as e:
            logger.error(
                f"CraftingVillage (Steinmetz): Fehler beim Bearbeiten von Kachel {tile}: {e}"
            )
            return False

    async def finish_grid(
        self, slot: int, stock_service: Any | None = None
    ) -> dict[str, Any] | None:
        """Complete a fully chiseled sculpture and collect rewards (stonemason_finish_grid)."""
        logger.info(f"CraftingVillage (Steinmetz): Schließe Werkbank-Slot {slot} ab (Ernte/Fertigstellung)...")
        try:
            res = await self.client.api_call(
                "farm",
                {
                    "mode": "stonemason_finish_grid",
                    "slot": int(slot),
                },
            )
            if not res or ("datablock" not in res and "updateblock" not in res):
                logger.warning(
                    f"CraftingVillage (Steinmetz): Slot {slot} konnte nicht abgeschlossen werden. Response: {res}"
                )
                return None

            ub = res.get("updateblock", {})
            self._update_from_updateblock(ub, stock_service=stock_service)

            reward = res.get("datablock") if isinstance(res.get("datablock"), dict) else {}
            if not reward and isinstance(res.get("reward"), dict):
                reward = res["reward"]

            logger.info(f"CraftingVillage (Steinmetz): Slot {slot} erfolgreich abgeschlossen! Belohnung: {reward}")
            # Refresh stonemason remote state
            await self.init_remote()
            return reward if isinstance(reward, dict) else {"success": True}
        except Exception as e:
            logger.error(
                f"CraftingVillage (Steinmetz): Fehler beim Fertigstellen von Slot {slot}: {e}"
            )
            return None

    async def buy_stonemason_tool(
        self,
        tool_id: int = 1,
        currency_amount: int = 500,
        stock_service: Any | None = None,
    ) -> bool:
        """Buy stonemason tools (chisels) using kT (stonemason_buy_tool). Strictly protects coins."""
        tid = str(tool_id)
        if self.snapshot and self.snapshot.stonemason:
            tool_cfg = self.snapshot.stonemason.tools_config.get(tid)
            if tool_cfg:
                if settings.crafting_village.coin_protection and tool_cfg.costs_coins:
                    logger.warning(
                        f"CraftingVillage (Steinmetz): Kauf von Werkzeug {tid} abgebrochen (erfordert Coins!)."
                    )
                    return False
                if str(currency_amount) not in tool_cfg.money and currency_amount not in tool_cfg.money:
                    logger.warning(
                        f"CraftingVillage (Steinmetz): Ungültiger Preis {currency_amount} kT für Werkzeug {tid} (Gültig: {list(tool_cfg.money.keys())})."
                    )
                    return False

        # Check credit budget
        if stock_service and hasattr(stock_service, "bar"):
            bar = float(stock_service.bar or 0)
            min_credit = float(settings.stock.min_credit_kt or 500.0)
            if bar - currency_amount < min_credit:
                logger.warning(
                    f"CraftingVillage (Steinmetz): Barvermögen ({bar:.2f} kT) zu gering für Meißelkauf ({currency_amount} kT) bei Mindestguthaben {min_credit:.2f} kT."
                )
                return False

        logger.info(f"CraftingVillage (Steinmetz): Kaufe Meißel-Paket {tid} für {currency_amount} kT...")
        try:
            res = await self.client.api_call(
                "farm",
                {
                    "mode": "stonemason_buy_tool",
                    "tool": int(tool_id),
                    "currency": int(currency_amount),
                },
            )
            if not res or ("updateblock" not in res and res.get("datablock") != 1):
                logger.warning(
                    f"CraftingVillage (Steinmetz): Werkzeugkauf {tid} fehlgeschlagen. Response: {res}"
                )
                return False

            ub = res.get("updateblock", {})
            self._update_from_updateblock(ub, stock_service=stock_service)
            logger.info(
                f"CraftingVillage (Steinmetz): Werkzeug {tid} ({currency_amount} kT) erfolgreich gekauft."
            )
            return True
        except Exception as e:
            logger.error(f"CraftingVillage (Steinmetz): Fehler beim Kauf von Werkzeug {tid}: {e}")
            return False

    async def serve_stonemason(self, stock_service: Any | None = None) -> dict[str, Any]:
        """Automate Stonemason workshop: finish completed grids, chisel tiles, and assign waiting customers."""
        if not settings.crafting_village.auto_stonemason:
            return {"status": "disabled"}

        if not self.snapshot or not self.snapshot.stonemason:
            return {"status": "skipped", "reason": "no_snapshot"}

        sm = self.snapshot.stonemason
        mine = self.snapshot.mine

        finished_count = 0
        chiseled_count = 0
        assigned_count = 0
        bought_tools_count = 0

        # 1. Fertige Werkbänke abschließen
        for cust in list(sm.customers.values()):
            if not cust.is_assigned:
                continue
            pcfg = sm.products_config.get(cust.pid)
            if not pcfg or not pcfg.allowed:
                continue

            tiles = cust.tiles
            all_chiseled = all(str(t) in tiles for t in pcfg.allowed)
            all_done = all_chiseled and all(
                (isinstance(tiles.get(str(t)), dict) and tiles[str(t)].get("remain", 0) <= 0)
                for t in pcfg.allowed
            )

            if all_done:
                slot_num = int(cust.slot)
                logger.info(
                    f"CraftingVillage (Steinmetz): Skulptur für {cust.name} (Slot {slot_num}, {pcfg.name}) ist fertiggestellt! Schließe ab..."
                )
                res = await self.finish_grid(slot=slot_num, stock_service=stock_service)
                if res:
                    finished_count += 1
                    sm = self.snapshot.stonemason
                    mine = self.snapshot.mine

        # 2. Prüfen, ob aktuell bereits ein Meißel-Vorgang läuft (is_working)
        # Wenn kein Vorgang aktiv ist, nächste erlaubte Kachel anschlagen
        if not sm.is_working:
            for cust in list(sm.customers.values()):
                if not cust.is_assigned:
                    continue
                pcfg = sm.products_config.get(cust.pid)
                if not pcfg or not pcfg.allowed:
                    continue

                tiles = cust.tiles
                uncarved = [t for t in pcfg.allowed if str(t) not in tiles]
                if uncarved:
                    # Wir benötigen Werkzeug 1 (Meißel)
                    tool_count = sm.tools.get("1", 0)
                    if tool_count <= 0:
                        if settings.crafting_village.auto_buy_stonemason_tools:
                            logger.info("CraftingVillage (Steinmetz): Keine Meißel vorhanden. Kaufe Meißel-Paket...")
                            bought = await self.buy_stonemason_tool(
                                tool_id=1, currency_amount=500, stock_service=stock_service
                            )
                            if not bought:
                                bought = await self.buy_stonemason_tool(
                                    tool_id=1, currency_amount=100, stock_service=stock_service
                                )
                            if bought:
                                bought_tools_count += 1
                                sm = self.snapshot.stonemason

                    tool_count = sm.tools.get("1", 0)
                    if tool_count > 0:
                        next_tile = uncarved[0]
                        ok = await self.chisel_tile(
                            customer_id=cust.id,
                            tile=next_tile,
                            tool_id=1,
                            stock_service=stock_service,
                        )
                        if ok:
                            chiseled_count += 1
                            sm = self.snapshot.stonemason
                            break

        # 3. Freie, freigeschaltete Werkbänke mit wartenden Kunden belegen
        current_level = sm.level
        unlocked_slots = [
            int(sid)
            for sid, scfg in sm.slots_config.items()
            if scfg.level <= current_level
        ]
        unlocked_slots.sort()

        occupied_slots = {int(c.slot) for c in sm.customers.values() if c.is_assigned}

        for slot_id in unlocked_slots:
            if slot_id in occupied_slots:
                continue

            waiting_customers = [c for c in sm.customers.values() if c.is_waiting]
            if not waiting_customers:
                break

            eligible_customers: list[tuple[StonemasonCustomer, StonemasonProductConfig, float]] = []
            stock_sandstone = mine.stock.get("sandstone", 0) if mine else 0
            stock_granite = mine.stock.get("granite", 0) if mine else 0
            reward_prio = getattr(settings.crafting_village, "stonemason_reward_priority", "overall")

            for wc in waiting_customers:
                pcfg = sm.products_config.get(wc.pid)
                if not pcfg:
                    continue
                req_sandstone = pcfg.material.get("sandstone", 0)
                req_granite = pcfg.material.get("granite", 0)

                if stock_sandstone >= req_sandstone and stock_granite >= req_granite:
                    score = wc.get_reward_score(pcfg, priority=reward_prio)
                    eligible_customers.append((wc, pcfg, score))

            if not eligible_customers:
                logger.debug(
                    f"CraftingVillage (Steinmetz): Slot {slot_id} ist frei, aber kein wartender Kunde hat ausreichend Rohmaterialien im Lager."
                )
                continue

            # Wähle den Kunden mit der besten Belohnung (höchster Score)
            eligible_customers.sort(key=lambda x: x[2], reverse=True)
            best_customer, best_pcfg, best_score = eligible_customers[0]
            rewards = best_customer.calculate_rewards(best_pcfg)

            logger.info(
                f"CraftingVillage (Steinmetz): Bester Kunde für Slot {slot_id} ist '{best_customer.name}' "
                f"(ID: {best_customer.id}, Produkt: '{best_pcfg.name}', "
                f"Belohnung: {rewards['money']:.0f} kT, {rewards['points']:.0f} Farm-Pkt, {rewards['stonemason_points']:.1f} Steinmetz-Pkt, Score: {best_score:.1f})."
            )

            ok = await self.assign_customer_to_slot(
                customer_id=best_customer.id, slot=slot_id, stock_service=stock_service
            )
            if ok:
                assigned_count += 1
                occupied_slots.add(slot_id)
                sm = self.snapshot.stonemason
                mine = self.snapshot.mine

                # Sofort erste Kachel meißeln, falls Steinmetz frei und Meißel vorhanden
                if not sm.is_working and sm.tools.get("1", 0) > 0:
                    pcfg = sm.products_config.get(best_customer.pid)
                    if pcfg and pcfg.allowed:
                        ok_chisel = await self.chisel_tile(
                            customer_id=best_customer.id,
                            tile=pcfg.allowed[0],
                            tool_id=1,
                            stock_service=stock_service,
                        )
                        if ok_chisel:
                            chiseled_count += 1
                            sm = self.snapshot.stonemason

        return {
            "status": "ok",
            "finished": finished_count,
            "chiseled": chiseled_count,
            "assigned": assigned_count,
            "bought_tools": bought_tools_count,
        }

    async def serve(self, stock_service: Any | None = None) -> dict[str, Any]:
        """Perform automated cycle: harvest completed slots, refill energy, buy tools, and start mining."""
        logger.info("========== Handwerkerdorf: Starte Bewirtschaftungszyklus ==========")
        snap = await self.init_remote()
        if not snap or not snap.mine:
            return {"status": "skipped", "reason": "init_failed"}

        mine = snap.mine
        harvested_count = 0
        started_count = 0
        refilled_count = 0
        bought_tools_count = 0

        # 1. Ernten fertiger Slots
        if settings.crafting_village.auto_harvest:
            for slot_key, prod in list(mine.productions.items()):
                if prod.is_ready:
                    logger.info(
                        f"CraftingVillage: Slot {slot_key} ({prod.name}) ist fertig (remain: {prod.remain}s). Ernte..."
                    )
                    reward = await self.finish_production(slot=prod.slot, position=prod.position)
                    if reward:
                        harvested_count += 1
                        await self.init_remote()
                        mine = self.snapshot.mine

        # 2. Arbeiter-Energie auffüllen
        if settings.crafting_village.auto_refill_energy:
            for wid, worker in list(mine.workers.items()):
                if worker.energy < 100:
                    ok = await self.refill_worker_energy(worker_id=wid, stock_service=stock_service)
                    if ok:
                        refilled_count += 1
            if self.snapshot and self.snapshot.mine:
                mine = self.snapshot.mine

        # 3. Werkzeuge mit höchster Erfahrung nachkaufen, wenn keine mehr verfügbar sind
        if settings.crafting_village.auto_buy_tools:
            best_buyable = self.get_best_buyable_tool()
            if best_buyable:
                tool_name, _ = best_buyable
                # Wie viele dieses Spitzenwerkzeugs sind noch im Rucksack verfügbar?
                available_count = sum(
                    1
                    for item in mine.items.values()
                    if item.name == tool_name and item.stock > 0 and item.durability > 0
                )
                if available_count < 2:
                    needed_to_buy = 2 - available_count
                    ok = await self.buy_shop_item(
                        name=tool_name,
                        amount=needed_to_buy,
                        stock_service=stock_service,
                    )
                    if ok:
                        bought_tools_count += needed_to_buy
                        if self.snapshot and self.snapshot.mine:
                            mine = self.snapshot.mine

        # 4. Neuen Abbau starten
        if settings.crafting_village.auto_mine:
            target_mat = self.select_next_material()
            logger.debug(
                f"CraftingVillage: Ziel-Material laut Bestandsabgleich: {target_mat} "
                f"(Sandstein: {mine.stock.get('sandstone', 0)}, Granit: {mine.stock.get('granite', 0)})"
            )

            # Priorisiere Ziel-Material
            if target_mat == "granite":
                slots_to_check = [("2", "granite"), ("1", "sandstone")]
            else:
                slots_to_check = [("1", "sandstone"), ("2", "granite")]

            for s_id, mat in slots_to_check:
                existing_prod = mine.productions.get(s_id)
                if existing_prod and not existing_prod.is_ready:
                    continue

                tools = self.get_best_tools_for_mining(count=2)
                if len(tools) < 2:
                    logger.warning(
                        "CraftingVillage: Nicht genügend verfügbare Werkzeuge für Abbau (mindestens 2 benötigt)."
                    )
                    break

                worker = self.get_best_worker_for_material(mat)
                if not worker:
                    all_busy = all(w.is_working for w in mine.workers.values())
                    if all_busy:
                        logger.debug(
                            f"CraftingVillage: Slot {s_id} ({mat}) kann nicht gestartet werden: Alle Arbeiter sind aktuell beschäftigt."
                        )
                    else:
                        logger.debug(
                            f"CraftingVillage: Slot {s_id} ({mat}) kann nicht gestartet werden: Kein freier Arbeiter mit genügend Energie."
                        )
                    continue

                tool_ids = [t.id for t in tools]
                success = await self.start_mining(
                    slot=int(s_id),
                    position=1,
                    material=mat,
                    worker_id=worker.id,
                    item_ids=tool_ids,
                )
                if success:
                    started_count += 1
                    await self.init_remote()
                    if not self.snapshot or not self.snapshot.mine:
                        break
                    mine = self.snapshot.mine
                    continue

        # 5. Steinmetz-Werkstatt Automation
        stonemason_stats = {}
        if settings.crafting_village.auto_stonemason:
            stonemason_stats = await self.serve_stonemason(stock_service=stock_service)

        summary = self.get_summary()
        logger.info(
            f"========== Handwerkerdorf: Zyklus beendet (Geerntet: {harvested_count}, "
            f"Aufgeladen: {refilled_count}, Gekauft: {bought_tools_count}, Gestartet: {started_count}) =========="
        )
        return {
            "status": "ok",
            "harvested": harvested_count,
            "refilled": refilled_count,
            "bought_tools": bought_tools_count,
            "started": started_count,
            "stonemason": stonemason_stats,
            "summary": summary.model_dump(),
        }

    def get_summary(self) -> CraftingVillageSummary:
        """Provide a high-level summary for the dashboard and API."""
        if not self.snapshot or (not self.snapshot.mine and not self.snapshot.stonemason):
            return CraftingVillageSummary(active=False)

        mine = self.snapshot.mine
        workers_count = len(mine.workers) if mine else 0
        working_count = sum(1 for w in mine.workers.values() if w.is_working) if mine else 0
        active_prod_count = len(mine.productions) if mine else 0
        ready_prod_count = sum(1 for p in mine.productions.values() if p.is_ready) if mine else 0

        sm = self.snapshot.stonemason
        sm_level = sm.level if sm else 0
        sm_points = sm.points if sm else 0
        sm_customers = sm.waiting_customers_count if sm else 0
        sm_slots = sm.active_slots_count if sm else 0

        return CraftingVillageSummary(
            active=True,
            mine_level=mine.level if mine else 1,
            workers_count=workers_count,
            working_workers_count=working_count,
            active_productions_count=active_prod_count,
            ready_productions_count=ready_prod_count,
            sandstone_stock=mine.stock.get("sandstone", 0) if mine else 0,
            granite_stock=mine.stock.get("granite", 0) if mine else 0,
            stonemason_level=sm_level,
            stonemason_points=sm_points,
            stonemason_customers_count=sm_customers,
            stonemason_active_slots_count=sm_slots,
            last_updated=self.snapshot.last_updated,
        )
