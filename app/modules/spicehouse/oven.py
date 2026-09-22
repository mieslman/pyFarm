import json
from typing import Any
from loguru import logger

from app.core.client import MFFGameClient
from app.modules.spicehouse.models import OvenSlotInfo, SPICE_RAW_TO_DRIED
from app.modules.spicehouse.solver import SpiceQuestSolver
from app.services.stock_service import StockService


class SpiceOvenService:
    """Manages the drying oven (Trockenofen) in the Gewürzhaus on Farm 10."""

    def __init__(self, client: MFFGameClient, farm_id: int = 10):
        self.client = client
        self.farm_id = farm_id
        self.oven_remain: int = 0
        self.oven_is_ready: bool = False
        self.oven_is_idle: bool = True
        self.lines: dict[int, OvenSlotInfo] = {}

    def update(
        self,
        oven_data: Any,
        oven_slots: dict[str, Any] | None,
        oven_levels_config: dict[str, Any] | None,
    ):
        """Update oven status from spicehouse_init response."""
        # 1. Update running/ready status
        if isinstance(oven_data, dict):
            remain = int(oven_data.get("remain", 0))
            self.oven_remain = remain
            self.oven_is_ready = remain <= 0
            self.oven_is_idle = False
        else:
            # 0 or None indicates empty / idle oven
            self.oven_remain = 0
            self.oven_is_ready = False
            self.oven_is_idle = True

        # 2. Update oven lines configuration & capacities
        self.lines = {}
        if oven_slots and isinstance(oven_slots, dict):
            for line_str, slot_data in oven_slots.items():
                if str(line_str).isdigit() and isinstance(slot_data, dict):
                    line_nr = int(line_str)
                    lvl = int(slot_data.get("level", 1))
                    is_rented = "duration" in slot_data or "buydate" in slot_data
                    remain_rent = int(slot_data.get("remain", 0))

                    # Capacity lookup
                    cap = 10
                    if oven_levels_config and str(lvl) in oven_levels_config:
                        cap = int(oven_levels_config[str(lvl)].get("capacity", 10))

                    self.lines[line_nr] = OvenSlotInfo(
                        line=line_nr,
                        level=lvl,
                        capacity=cap,
                        slots=4,
                        is_rented=is_rented,
                        remain=remain_rent,
                    )

    async def harvest(self) -> bool:
        """Collect dried spices from the oven when drying is completed."""
        if not self.oven_is_ready:
            return False

        logger.info("Gewürzhaus: Leere fertigen Trockenofen (spicehouse_open_oven)...")
        try:
            res = await self.client.api_call("farm", {"mode": "spicehouse_open_oven"})
            datablock = res.get("datablock")
            success = datablock == 1 or datablock == [1] or (isinstance(datablock, list) and 1 in datablock)
            if success:
                logger.info("Gewürzhaus: Trockenofen erfolgreich geleert (+100 Gewürzstreuer-Punkte).")
                self.oven_is_ready = False
                self.oven_is_idle = True
                return True
        except Exception as e:
            logger.warning(f"Gewürzhaus: Fehler beim Leeren des Trockenofens: {e}")

        return False

    def plan_oven_loading(
        self,
        stock_service: StockService,
        quest_solver: SpiceQuestSolver,
        quest_demands: dict[int, int],
        min_crop_reserve: int = 500,
    ) -> dict[str, dict[str, dict[str, int]]]:
        """Plan loading configuration for available oven lines based on Quest 6 priorities and stock surplus."""
        available_lines = [line for line in self.lines.values() if line.is_available and line.line in (1, 2, 3)]
        if not available_lines:
            logger.debug("Gewürzhaus: Keine verfügbaren Ofenbleche vorhanden.")
            return {}

        # 1. Priorität 1: Quest 6 Bedarfe
        quest_prio_items = quest_solver.get_oven_quest_priorities(
            quest_demands,
            stock_service,
            min_crop_reserve=min_crop_reserve,
        )
        quest_pids_order = [pid for pid, _ in quest_prio_items]

        # 2. Priorität 2: Größte Lagermengen
        valid_raw_pids = list(SPICE_RAW_TO_DRIED.keys())
        stock_surpluses: list[tuple[int, int]] = []
        for pid in valid_raw_pids:
            cur_amt = stock_service.get_farm_amount(self.farm_id, pid)
            surplus = max(0, cur_amt - min_crop_reserve)
            if surplus > 0:
                stock_surpluses.append((pid, surplus))

        # Sort descending by surplus amount
        stock_surpluses.sort(key=lambda x: x[1], reverse=True)
        fallback_pids_order = [pid for pid, _ in stock_surpluses if pid not in quest_pids_order]

        ordered_candidates = quest_pids_order + fallback_pids_order
        if not ordered_candidates:
            logger.debug(
                f"Gewürzhaus: Keine Rohgewürze über der Sicherheitsreserve ({min_crop_reserve}) "
                f"auf Farm {self.farm_id} verfügbar."
            )
            return {}

        # Track remaining surplus per candidate locally
        remaining_surplus = {
            pid: max(0, stock_service.get_farm_amount(self.farm_id, pid) - min_crop_reserve)
            for pid in ordered_candidates
        }

        setup: dict[str, dict[str, dict[str, int]]] = {}

        # Fill available lines
        for line in available_lines:
            line_cap = line.capacity
            line_str = str(line.line)
            slot_idx = 1

            for pid in ordered_candidates:
                surplus = remaining_surplus.get(pid, 0)
                if surplus <= 0:
                    continue

                load = min(line_cap, surplus)
                if load > 0:
                    if line_str not in setup:
                        setup[line_str] = {}

                    setup[line_str][str(slot_idx)] = {"pid": pid, "amount": load}
                    remaining_surplus[pid] -= load
                    line_cap -= load
                    slot_idx += 1

                if line_cap <= 0 or slot_idx > line.slots:
                    break

        return setup

    async def produce(
        self,
        stock_service: StockService,
        quest_solver: SpiceQuestSolver,
        quest_demands: dict[int, int],
        min_crop_reserve: int = 500,
    ) -> bool:
        """Fill and start the drying oven according to priority rules:
        1. Prioritize raw spices required for active Questreihe 6.
        2. Otherwise, use raw spices with the largest available stock on Farm 10 (> reserve).
        """
        if not self.oven_is_idle:
            return False

        setup = self.plan_oven_loading(
            stock_service=stock_service,
            quest_solver=quest_solver,
            quest_demands=quest_demands,
            min_crop_reserve=min_crop_reserve,
        )
        if not setup:
            return False

        total_loaded = sum(
            item["amount"]
            for line_data in setup.values()
            for item in line_data.values()
        )

        logger.info(
            f"Gewürzhaus: Starte Trockenofen mit {total_loaded} Gewürz-Einheiten "
            f"über {len(setup)} Blech(e)..."
        )
        try:
            res = await self.client.api_call(
                "farm",
                {
                    "mode": "spicehouse_start_oven",
                    "setup": json.dumps(setup),
                },
            )
            datablock = res.get("datablock")
            success = datablock == 1 or datablock == [1] or (isinstance(datablock, list) and 1 in datablock)
            if success:
                logger.info(f"Gewürzhaus: Trockenofen erfolgreich gestartet ({total_loaded} Einheiten).")
                for line_data in setup.values():
                    for slot_item in line_data.values():
                        stock_service.deduct_stock(
                            slot_item["pid"], slot_item["amount"], farm_id=self.farm_id
                        )
                self.oven_is_idle = False
                self.oven_remain = 19800
                return True
        except Exception as e:
            logger.warning(f"Gewürzhaus: Fehler beim Starten des Trockenofens: {e}")

        return False
