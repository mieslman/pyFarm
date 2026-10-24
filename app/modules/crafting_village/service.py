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
        raw_workers = data.get("workers", {})
        if isinstance(raw_workers, dict):
            for wid, w_info in raw_workers.items():
                if not isinstance(w_info, dict):
                    continue
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
                    productionid=(
                        str(w_info.get("productionid")) if w_info.get("productionid") else None
                    ),
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
                                items={str(k): int(v) for k, v in p_info.get("items", {}).items()},
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

            mine_snap = self._parse_mine_data(data, config)
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

            # Auswertung des Produkts & der benötigten Menge
            chosen_pid = pid
            chosen_amount = amount

            if chosen_pid is None or chosen_amount is None:
                energy_map = mine.validenergyproducts
                # Bevorzuge Grundpflanzen (1: Karotte, 2: Gurke, 3: Radieschen etc.)
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

    async def serve(self, stock_service: Any | None = None) -> dict[str, Any]:
        """Perform automated cycle: harvest completed slots, refill energy, and start new mining operations."""
        logger.info("========== Handwerkerdorf: Starte Bewirtschaftungszyklus ==========")
        snap = await self.init_remote()
        if not snap or not snap.mine:
            return {"status": "skipped", "reason": "init_failed"}

        mine = snap.mine
        harvested_count = 0
        started_count = 0
        refilled_count = 0

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

        # 3. Neuen Abbau starten
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
                    logger.debug(
                        f"CraftingVillage: Kein verfügbarer Arbeiter mit genügend Energie für {mat}."
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
                    mine = self.snapshot.mine
                    break

        summary = self.get_summary()
        logger.info(
            f"========== Handwerkerdorf: Zyklus beendet (Geerntet: {harvested_count}, "
            f"Aufgeladen: {refilled_count}, Gestartet: {started_count}) =========="
        )
        return {
            "status": "ok",
            "harvested": harvested_count,
            "refilled": refilled_count,
            "started": started_count,
            "summary": summary.model_dump(),
        }

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
