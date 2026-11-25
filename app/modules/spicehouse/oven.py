import json
from typing import Any

from loguru import logger

from app.core.client import MFFGameClient
from app.modules.spicehouse.models import (
    SPICE_DRIED_TO_GROUND,
    SPICE_RAW_TO_DRIED,
    OvenSlotInfo,
)
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

    async def harvest(self, stock_service: StockService | None = None) -> bool:
        """Collect dried spices from the oven when drying is completed."""
        if not self.oven_is_ready:
            return False

        logger.info("Gewürzhaus: Leere fertigen Trockenofen (spicehouse_open_oven)...")
        try:
            res = await self.client.api_call("farm", {"mode": "spicehouse_open_oven"})
            datablock = res.get("datablock")
            updateblock = res.get("updateblock") if isinstance(res.get("updateblock"), dict) else {}
            sh_update = updateblock.get("spicehouse") if isinstance(updateblock, dict) else None
            oven_cleared_in_update = (
                isinstance(sh_update, dict)
                and "oven" in sh_update
                and sh_update.get("oven") in (0, None, False)
            )

            success = (
                datablock == 1
                or datablock == [1]
                or (isinstance(datablock, list) and 1 in datablock)
                or (
                    isinstance(datablock, dict)
                    and (
                        datablock.get("status") == 1
                        or "spicehouse_points" in datablock
                        or any(str(k).isdigit() for k in datablock)
                    )
                )
                or (res.get("status") == "ok" and oven_cleared_in_update)
            )
            if success:
                logger.info(
                    "Gewürzhaus: Trockenofen erfolgreich geleert (+100 Gewürzstreuer-Punkte)."
                )
                if stock_service:
                    if isinstance(updateblock, dict) and "stock" in updateblock:
                        stock_service.update(res)
                    elif isinstance(datablock, dict):
                        for k, v in datablock.items():
                            if str(k).isdigit():
                                try:
                                    h_pid = int(k)
                                    h_amt = int(v)
                                    if self.farm_id not in stock_service.farm_stocks:
                                        stock_service.farm_stocks[self.farm_id] = {}
                                    stock_service.farm_stocks[self.farm_id][h_pid] = (
                                        stock_service.farm_stocks[self.farm_id].get(h_pid, 0)
                                        + h_amt
                                    )
                                    if h_pid in stock_service.products:
                                        stock_service.products[h_pid].amount += h_amt
                                except (ValueError, TypeError):
                                    pass
                self.oven_is_ready = False
                self.oven_is_idle = True
                self.oven_remain = 0
                return True
            else:
                logger.warning(
                    f"Gewürzhaus: Unerwartete Antwort beim Leeren des Trockenofens (datablock={datablock})."
                )
        except Exception as e:
            logger.warning(f"Gewürzhaus: Fehler beim Leeren des Trockenofens: {e}")

        return False

    def plan_oven_loading(
        self,
        stock_service: StockService,
        quest_solver: SpiceQuestSolver,
        quest_demands: dict[int, int],
        min_crop_reserve: int = 500,
        min_dried_reserve: int = 500,
    ) -> dict[str, dict[str, dict[str, int]]]:
        """Plan loading configuration for available oven lines based on Quest 6 priorities and stock surplus."""
        available_lines = [
            line for line in self.lines.values() if line.is_available and line.line in (1, 2, 3)
        ]
        if not available_lines:
            logger.debug("Gewürzhaus: Keine verfügbaren Ofenbleche vorhanden.")
            return {}

        # 1. Priorität 1: Quest 6 Bedarfe
        quest_prio_items = quest_solver.get_oven_quest_priorities(
            quest_demands,
            stock_service,
            min_crop_reserve=min_crop_reserve,
            min_dried_reserve=min_dried_reserve,
        )
        quest_pids_order = [pid for pid, _ in quest_prio_items]

        # 2. Priorität 2: Gleichverteilung (Least-Stock-First)
        # Bevorzuge Rohgewürze über der Mindestreserve, deren weiterverarbeiteter Bestand
        # (getrocknet + gemahlen) am niedrigsten ist. Bei Gleichstand größter Roh-Überschuss zuerst.
        valid_raw_pids = list(SPICE_RAW_TO_DRIED.keys())
        has_farm1_stocks = bool(
            getattr(stock_service, "farm_stocks", None) and 1 in stock_service.farm_stocks
        )
        stock_candidates: list[tuple[int, int, int, int]] = []
        for pid in valid_raw_pids:
            cur_amt = stock_service.get_farm_amount(self.farm_id, pid)
            surplus = max(0, cur_amt - min_crop_reserve)
            if surplus > 0:
                dried_pid = SPICE_RAW_TO_DRIED[pid]
                ground_pid = SPICE_DRIED_TO_GROUND.get(dried_pid)
                dried_stock = stock_service.get_farm_amount(self.farm_id, dried_pid)
                ground_f10 = (
                    stock_service.get_farm_amount(self.farm_id, ground_pid) if ground_pid else 0
                )
                ground_f1 = (
                    stock_service.farm_stocks[1].get(ground_pid, 0)
                    if (has_farm1_stocks and ground_pid)
                    else 0
                )
                downstream_total = dried_stock + ground_f10 + ground_f1
                stock_candidates.append((pid, downstream_total, dried_stock, surplus))

        # Sort ascending by downstream stock (dried + ground), then dried stock, tie-break by largest raw surplus
        stock_candidates.sort(key=lambda x: (x[1], x[2], -x[3]))
        fallback_pids_order = [pid for pid, _, _, _ in stock_candidates if pid not in quest_pids_order]

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
        min_dried_reserve: int = 500,
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
            min_dried_reserve=min_dried_reserve,
        )
        if not setup:
            return False

        total_loaded = sum(
            item["amount"] for line_data in setup.values() for item in line_data.values()
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
            success = (
                datablock == 1
                or datablock == [1]
                or (isinstance(datablock, list) and 1 in datablock)
            )
            if success:
                logger.info(
                    f"Gewürzhaus: Trockenofen erfolgreich gestartet ({total_loaded} Einheiten)."
                )
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
