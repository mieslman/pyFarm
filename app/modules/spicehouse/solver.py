from typing import Any
from loguru import logger

from app.modules.spicehouse.models import (
    SPICE_DRIED_TO_GROUND,
    SPICE_DRIED_TO_RAW,
    SPICE_GROUND_TO_DRIED,
    SPICE_RAW_TO_DRIED,
)
from app.services.stock_service import StockService


class SpiceQuestSolver:
    """Solver for aligning Gewürzhaus production with active requirements of Questreihe 6."""

    def __init__(self, farm_id: int = 10):
        self.farm_id = farm_id

    def extract_quest6_demands(
        self,
        quest_status_main: dict[str, Any] | None = None,
        quest_requirements: dict[int, int] | None = None,
    ) -> dict[int, int]:
        """Extract requested product demands {pid: amount} specific to Campaign 6 (Gourmetküchequest)."""
        demands: dict[int, int] = {}

        # 1. Parse from quest_status_main["6"]
        if quest_status_main and isinstance(quest_status_main, dict):
            q6_info = quest_status_main.get("6") or quest_status_main.get(6)
            if isinstance(q6_info, dict):
                # Structure: data['1'][0] = {pid_str: amount}
                q_data = q6_info.get("data", {})
                if isinstance(q_data, dict):
                    prods_entry = q_data.get("1", [])
                    if isinstance(prods_entry, list) and len(prods_entry) > 0:
                        req_map = prods_entry[0]
                        if isinstance(req_map, dict):
                            for p_str, amt in req_map.items():
                                if str(p_str).isdigit():
                                    try:
                                        demands[int(p_str)] = int(amt)
                                    except (ValueError, TypeError):
                                        pass

                # Fallback: reqs/products/needs keys
                for req_key in ("reqs", "products", "needs"):
                    if req_key in q6_info and isinstance(q6_info[req_key], dict):
                        for p_str, amt in q6_info[req_key].items():
                            if str(p_str).isdigit():
                                try:
                                    demands[int(p_str)] = int(amt)
                                except (ValueError, TypeError):
                                    pass

        # 2. Integrate any demands from quest_requirements that are in spice categories
        if quest_requirements:
            all_spice_pids = (
                set(SPICE_RAW_TO_DRIED.keys())
                | set(SPICE_DRIED_TO_GROUND.keys())
                | set(SPICE_DRIED_TO_GROUND.values())
            )
            for p, amt in quest_requirements.items():
                if p in all_spice_pids and p not in demands:
                    demands[p] = amt

        return demands

    def get_mill_quest_priorities(
        self,
        quest_demands: dict[int, int],
        stock_service: StockService,
    ) -> list[tuple[int, int]]:
        """Identify which dried spices should be milled for Quest 6.

        Returns:
            List of tuples `(dried_pid, missing_amount)` ordered by priority.
        """
        priorities: list[tuple[int, int]] = []

        for req_pid, needed_total in quest_demands.items():
            # Check if this demand is a ground spice
            if req_pid in SPICE_GROUND_TO_DRIED:
                dried_pid = SPICE_GROUND_TO_DRIED[req_pid]
                # Check deficiency on Farm 1
                current_ground_main = stock_service.get_farm_amount(1, req_pid)
                missing = max(0, needed_total - current_ground_main)
                if missing > 0:
                    # Check if dried precursor is available on Farm 10
                    avail_dried = stock_service.get_farm_amount(self.farm_id, dried_pid)
                    if avail_dried > 0:
                        priorities.append((dried_pid, missing))
                        logger.debug(
                            f"SpiceQuestSolver (Mühle): Quest 6 verlangt PID {req_pid} "
                            f"(Fehlmenge: {missing}). Verwende getrocknete Vorstufe PID {dried_pid} "
                            f"(Vorrat auf Farm {self.farm_id}: {avail_dried})."
                        )

        return priorities

    def get_oven_quest_priorities(
        self,
        quest_demands: dict[int, int],
        stock_service: StockService,
        min_crop_reserve: int = 500,
    ) -> list[tuple[int, int]]:
        """Identify which raw spices should be dried in the oven for Quest 6.

        Returns:
            List of tuples `(raw_pid, missing_amount)` ordered by priority.
        """
        priorities: list[tuple[int, int]] = []

        for req_pid, needed_total in quest_demands.items():
            raw_pid: int | None = None
            missing: int = 0

            # Case A: Quest requires dried spice directly (PIDs 1107..1116)
            if req_pid in SPICE_DRIED_TO_RAW:
                current_dried_main = stock_service.get_farm_amount(1, req_pid)
                missing = max(0, needed_total - current_dried_main)
                if missing > 0:
                    raw_pid = SPICE_DRIED_TO_RAW[req_pid]

            # Case B: Quest requires ground spice (PIDs 1117..1126)
            elif req_pid in SPICE_GROUND_TO_DRIED:
                current_ground_main = stock_service.get_farm_amount(1, req_pid)
                missing = max(0, needed_total - current_ground_main)
                if missing > 0:
                    dried_pid = SPICE_GROUND_TO_DRIED[req_pid]
                    # If we don't have enough dried spice on Farm 10, we must dry raw spice!
                    avail_dried = stock_service.get_farm_amount(self.farm_id, dried_pid)
                    if avail_dried < missing:
                        raw_pid = SPICE_DRIED_TO_RAW.get(dried_pid)

            if raw_pid and missing > 0:
                avail_raw = stock_service.get_farm_amount(self.farm_id, raw_pid)
                usable_surplus = max(0, avail_raw - min_crop_reserve)
                if usable_surplus > 0:
                    if (raw_pid, missing) not in priorities:
                        priorities.append((raw_pid, missing))
                        logger.debug(
                            f"SpiceQuestSolver (Ofen): Quest 6 verlangt PID {req_pid} "
                            f"(Fehlmenge: {missing}). Trockne Rohgewürz PID {raw_pid} "
                            f"(Vorrat auf Farm {self.farm_id}: {avail_raw}, Überschuss: {usable_surplus})."
                        )

        return priorities

    @staticmethod
    def get_required_dried_spice(ground_pid: int) -> int | None:
        """Get dried precursor product ID for a ground spice."""
        return SPICE_GROUND_TO_DRIED.get(ground_pid)

    @staticmethod
    def get_required_raw_spice(spice_pid: int) -> int | None:
        """Get raw precursor product ID for a dried or ground spice."""
        if spice_pid in SPICE_DRIED_TO_RAW:
            return SPICE_DRIED_TO_RAW[spice_pid]
        if spice_pid in SPICE_GROUND_TO_DRIED:
            dried = SPICE_GROUND_TO_DRIED[spice_pid]
            return SPICE_DRIED_TO_RAW.get(dried)
        return None
