import time
from typing import Any
from loguru import logger

from app.core.client import MFFGameClient
from app.modules.spicehouse.models import (
    MillSlotInfo,
    SPICE_DRIED_TO_GROUND,
    SPICE_MILL_DURATIONS,
)
from app.modules.spicehouse.solver import SpiceQuestSolver
from app.services.stock_service import StockService


class SpiceMillService:
    """Manages the spice mills (Gewürzmühlen) in the Gewürzhaus on Farm 10."""

    def __init__(self, client: MFFGameClient, farm_id: int = 10):
        self.client = client
        self.farm_id = farm_id
        self.slots: dict[int, MillSlotInfo] = {}

    def update(
        self,
        mill_slots_data: dict[str, Any] | None,
        mill_levels_config: dict[str, Any] | None = None,
        server_time: int = 0,
        products_config: dict[str, Any] | None = None,
    ):
        """Update mill slots status from spicehouse_init response.

        Calculates completed milling output units and remaining time based on
        start timestamp, product unit duration, and current server time.
        """
        self.slots = {}
        if not mill_slots_data or not isinstance(mill_slots_data, dict):
            return

        now = server_time if server_time > 0 else int(time.time())

        for slot_str, slot_data in mill_slots_data.items():
            if str(slot_str).isdigit() and isinstance(slot_data, dict):
                slot_nr = int(slot_str)
                lvl = int(slot_data.get("level", 1))
                is_rented = "remain" in slot_data and slot_nr == 4

                # Capacity lookup
                cap = 10
                if mill_levels_config and str(lvl) in mill_levels_config:
                    cap = int(mill_levels_config[str(lvl)].get("capacity", 10))
                else:
                    cap = max(10, lvl * 10)

                raw_pid = slot_data.get("pid")
                pid = int(raw_pid) if raw_pid and str(raw_pid).isdigit() and int(raw_pid) > 0 else None
                amt = int(slot_data.get("amount", 0))
                amt_orig = int(slot_data.get("amount_original", 0))
                start = int(slot_data.get("start", 0))
                raw_output = int(slot_data.get("output", 0))
                raw_remain = int(slot_data.get("remain", 0))
                duration = int(slot_data.get("duration", 0))

                # Determine unit milling duration
                if duration > 0:
                    unit_duration = duration
                elif (
                    products_config
                    and pid
                    and str(pid) in products_config
                    and "duration" in products_config[str(pid)]
                ):
                    unit_duration = int(products_config[str(pid)]["duration"])
                elif pid and pid in SPICE_MILL_DURATIONS:
                    unit_duration = SPICE_MILL_DURATIONS[pid]
                else:
                    unit_duration = 600

                # Calculate milling progress and finished units
                if amt > 0 and start > 0 and unit_duration > 0:
                    elapsed = max(0, now - start)
                    finished_units = min(amt, elapsed // unit_duration)
                    output = max(raw_output, finished_units)
                    total_duration = amt * unit_duration
                    remain = max(0, total_duration - elapsed)
                else:
                    output = raw_output
                    remain = raw_remain

                self.slots[slot_nr] = MillSlotInfo(
                    slot=slot_nr,
                    level=lvl,
                    capacity=cap,
                    pid=pid,
                    amount=amt,
                    amount_original=amt_orig,
                    output=output,
                    remain=remain,
                    duration=unit_duration,
                    start=start,
                    is_rented=is_rented,
                )

    async def harvest(self, stock_service: StockService | None = None) -> int:
        """Collect finished ground spices from mills where output > 0."""
        harvested_count = 0

        for slot in self.slots.values():
            if slot.is_ready_to_harvest:
                logger.info(
                    f"Gewürzhaus: Ernte Mühle {slot.slot} ({slot.output} Einheiten fertig gemahlen)..."
                )
                try:
                    res = await self.client.api_call(
                        "farm",
                        {"mode": "spicehouse_harvest_mill", "slot": slot.slot},
                    )
                    datablock = res.get("datablock")
                    success = (
                        datablock == 1
                        or datablock == [1]
                        or (isinstance(datablock, list) and 1 in datablock)
                        or (isinstance(datablock, dict) and any(str(k).isdigit() for k in datablock))
                    )
                    if success:
                        logger.info(f"Gewürzhaus: Mühle {slot.slot} erfolgreich abgeerntet.")
                        harvested_amt = 0
                        if isinstance(datablock, dict):
                            for k, v in datablock.items():
                                if str(k).isdigit():
                                    try:
                                        h_pid = int(k)
                                        h_amt = int(v)
                                        harvested_amt += h_amt
                                        if stock_service:
                                            if self.farm_id not in stock_service.farm_stocks:
                                                stock_service.farm_stocks[self.farm_id] = {}
                                            stock_service.farm_stocks[self.farm_id][h_pid] = (
                                                stock_service.farm_stocks[self.farm_id].get(h_pid, 0) + h_amt
                                            )
                                            if h_pid in stock_service.products:
                                                stock_service.products[h_pid].amount += h_amt
                                    except (ValueError, TypeError):
                                        pass

                        if stock_service:
                            stock_service.update(res)

                        updateblock = res.get("updateblock", {})
                        sh_data = updateblock.get("spicehouse", {}) if isinstance(updateblock, dict) else {}
                        sh_mills = sh_data.get("data", {}).get("mill_slots", {}) if isinstance(sh_data, dict) else {}
                        slot_upd = sh_mills.get(str(slot.slot))

                        if slot_upd and isinstance(slot_upd, dict):
                            slot.amount = int(slot_upd.get("amount", 0))
                            slot.amount_original = int(slot_upd.get("amount_original", 0))
                            slot.start = int(slot_upd.get("start", 0))
                            slot.output = int(slot_upd.get("output", 0))
                        else:
                            if harvested_amt > 0:
                                slot.amount = max(0, slot.amount - harvested_amt)
                            else:
                                slot.amount = 0
                            slot.output = 0

                        if slot.amount == 0:
                            slot.amount_original = 0
                            slot.remain = 0

                        harvested_count += 1
                except Exception as e:
                    logger.warning(f"Gewürzhaus: Fehler beim Ernten von Mühle {slot.slot}: {e}")

        return harvested_count

    async def produce(
        self,
        stock_service: StockService,
        quest_solver: SpiceQuestSolver,
        quest_demands: dict[int, int],
    ) -> int:
        """Fill and start idle mills according to priority rules:
        1. Prioritize dried spices needed for active Questreihe 6.
        2. Otherwise, grind the dried spices with the largest stock on Farm 10.
        """
        # Find idle slots (never rent slot 4 with coins; use it only if already rented)
        idle_slots = [s for s in self.slots.values() if s.is_idle and s.is_available]
        if not idle_slots:
            return 0

        # 1. Priorität 1: Quest 6 Bedarfe
        quest_prio_items = quest_solver.get_mill_quest_priorities(quest_demands, stock_service)
        quest_pids_order = [pid for pid, _ in quest_prio_items]

        # 2. Priorität 2: Größte Bestände an getrockneten Gewürzen auf Farm 10
        valid_dried_pids = list(SPICE_DRIED_TO_GROUND.keys())
        stock_items: list[tuple[int, int]] = []
        for pid in valid_dried_pids:
            cur_amt = stock_service.get_farm_amount(self.farm_id, pid)
            if cur_amt > 0:
                stock_items.append((pid, cur_amt))

        # Sort descending by quantity
        stock_items.sort(key=lambda x: x[1], reverse=True)
        fallback_pids_order = [pid for pid, _ in stock_items if pid not in quest_pids_order]

        ordered_candidates = quest_pids_order + fallback_pids_order
        if not ordered_candidates:
            logger.debug(f"Gewürzhaus: Keine getrockneten Gewürze auf Farm {self.farm_id} zum Mahlen vorhanden.")
            return 0

        # Local tracking of remaining stock
        remaining_stock = {
            pid: stock_service.get_farm_amount(self.farm_id, pid)
            for pid in ordered_candidates
        }

        started_count = 0
        for slot in idle_slots:
            # Find candidate with available stock
            chosen_pid: int | None = None
            amount_to_load = 0

            for pid in ordered_candidates:
                avail = remaining_stock.get(pid, 0)
                if avail > 0:
                    chosen_pid = pid
                    amount_to_load = min(slot.capacity, avail)
                    break

            if chosen_pid and amount_to_load > 0:
                prod = stock_service.get_product(chosen_pid)
                p_name = prod.name if prod else f"PID {chosen_pid}"
                logger.info(
                    f"Gewürzhaus: Starte Mühle {slot.slot} mit {amount_to_load}x {p_name} (PID {chosen_pid})..."
                )
                try:
                    res = await self.client.api_call(
                        "farm",
                        {
                            "mode": "spicehouse_set_millslot",
                            "slot": slot.slot,
                            "pid": chosen_pid,
                            "amount": amount_to_load,
                        },
                    )
                    datablock = res.get("datablock")
                    success = (
                        datablock == 1
                        or datablock == [1]
                        or (isinstance(datablock, list) and 1 in datablock)
                        or (isinstance(datablock, dict) and datablock.get("status") == 1)
                    )
                    if success:
                        logger.info(
                            f"Gewürzhaus: Mühle {slot.slot} erfolgreich gestartet ({amount_to_load}x PID {chosen_pid})."
                        )
                        stock_service.deduct_stock(chosen_pid, amount_to_load, farm_id=self.farm_id)
                        stock_service.update(res)
                        remaining_stock[chosen_pid] -= amount_to_load
                        duration_unit = SPICE_MILL_DURATIONS.get(chosen_pid, 600)
                        slot.pid = chosen_pid
                        slot.amount = amount_to_load
                        slot.amount_original = amount_to_load
                        slot.output = 0
                        slot.remain = amount_to_load * duration_unit
                        slot.start = int(time.time())
                        started_count += 1
                except Exception as e:
                    logger.warning(f"Gewürzhaus: Fehler beim Starten von Mühle {slot.slot}: {e}")

        return started_count
