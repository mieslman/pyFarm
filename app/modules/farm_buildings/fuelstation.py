import math
from typing import Any

from loguru import logger

from app.config import FuelstationConfig, settings
from app.core.client import MFFGameClient
from app.core.exceptions import UpstreamAPIError
from app.models.fuelstation import FuelstationSlot, FuelstationState
from app.services.stock_service import StockService


class Fuelstation:
    """Controls the Biosprit-Anlage (Building ID 20).

    Automates:
    - Harvesting finished Biosprit canisters (PID 350) when remain <= 0.
    - Refilling empty slots with excess field crops to start production.
    """

    def __init__(
        self,
        client: MFFGameClient,
        farm_id: int = 4,
        position: int = 6,
        config: FuelstationConfig | None = None,
    ):
        self.client = client
        self.farm_id = farm_id
        self.position = position
        self.config = config or settings.fuelstation
        self.level: int = 1
        self.tokens: int = 0
        self.slots: dict[int, FuelstationSlot] = {}

    def update(self, building_data: dict[str, Any]) -> FuelstationState:
        """Parse slot states and active productions from getfarms building data."""
        data_obj = building_data.get("data", {})
        inner_data = data_obj.get("data", {}) if isinstance(data_obj, dict) else {}
        constants = data_obj.get("constants", {}) if isinstance(data_obj, dict) else {}
        slot_level_constants = constants.get("slot_level", {}) if isinstance(constants, dict) else {}

        self.level = int(inner_data.get("level", building_data.get("level", 1)))

        # Biosprit-Marken count
        try:
            self.tokens = int(inner_data.get("count", 0))
        except (ValueError, TypeError):
            self.tokens = 0

        # 1. Parse slots configuration
        raw_slots = inner_data.get("slots", {})
        if isinstance(raw_slots, dict):
            for slot_str, s_data in raw_slots.items():
                if not str(slot_str).isdigit() or not isinstance(s_data, dict):
                    continue
                slot_id = int(slot_str)
                s_level = int(s_data.get("level", 1))
                lvl_pts_left = int(s_data.get("points_left", 0))
                is_blocked = bool(s_data.get("block", 0))

                # Determine production limit: check constants first, fallback to s_data or level * 1_000_000
                prod_limit = int(
                    slot_level_constants.get(str(s_level), {}).get("limit", 0)
                )
                if prod_limit <= 0:
                    prod_limit = int(s_data.get("limit", s_data.get("production_limit", 0)))
                if prod_limit <= 0:
                    # In minimal tests without constants, use points_left if provided or level * 1_000_000
                    prod_limit = lvl_pts_left if lvl_pts_left > 0 else s_level * 1_000_000

                accepted: dict[int, int] = {}
                prods = s_data.get("products", {})
                if isinstance(prods, dict):
                    for pid_str, p_info in prods.items():
                        if str(pid_str).isdigit() and isinstance(p_info, dict):
                            accepted[int(pid_str)] = int(p_info.get("points", 1))

                # Calculate already inserted points from existing entries
                entries = s_data.get("entries", {})
                current_pts = 0
                if isinstance(entries, dict):
                    for pid_str, cnt in entries.items():
                        if str(pid_str).isdigit() and int(pid_str) in accepted:
                            try:
                                current_pts += accepted[int(pid_str)] * int(cnt)
                            except (ValueError, TypeError):
                                pass

                self.slots[slot_id] = FuelstationSlot(
                    slot_id=slot_id,
                    level=s_level,
                    production_limit=prod_limit,
                    current_points=current_pts,
                    level_points_left=lvl_pts_left,
                    is_blocked=is_blocked,
                    accepted_products=accepted,
                    busy=False,
                    remain=0,
                )

        # 2. Parse active productions
        productions: list[dict[str, Any]] = []
        raw_prod = building_data.get("production")
        if isinstance(raw_prod, list):
            productions = raw_prod
        elif isinstance(inner_data.get("production"), dict):
            productions = [
                {"slot": s_id, **p_info}
                for s_id, p_info in inner_data["production"].items()
                if isinstance(p_info, dict)
            ]

        for p_entry in productions:
            if isinstance(p_entry, dict) and "slot" in p_entry:
                try:
                    s_id = int(p_entry["slot"])
                    rem = int(p_entry.get("remain", 0))
                    if s_id in self.slots:
                        self.slots[s_id].busy = True
                        self.slots[s_id].remain = rem
                except (ValueError, TypeError):
                    continue

        return FuelstationState(
            farm_id=self.farm_id,
            position=self.position,
            level=self.level,
            tokens=self.tokens,
            slots=self.slots,
        )

    async def harvest(self) -> int:
        """Harvest completed Biosprit canisters for all finished slots."""
        if not self.config.auto_harvest:
            return 0

        harvested_count = 0
        for slot in self.slots.values():
            if slot.is_finished:
                logger.info(
                    f"Biosprit-Anlage (Farm {self.farm_id}, Pos {self.position}): "
                    f"Ernte fertigen Biosprit aus Slot {slot.slot_id}..."
                )
                try:
                    res = await self.client.api_call(
                        "farm",
                        {
                            "mode": "fuelstation_harvest",
                            "farm": self.farm_id,
                            "position": self.position,
                            "slot": slot.slot_id,
                        },
                    )
                    datablock = res.get("datablock")
                    is_ok = (
                        (isinstance(datablock, (list, tuple)) and len(datablock) > 0 and datablock[0] == 1)
                        or datablock == 1
                        or bool(res.get("updateblock"))
                    )
                    if is_ok:
                        harvested_count += 1
                        slot.busy = False
                        slot.remain = 0
                        logger.info(f"Biosprit-Anlage: Slot {slot.slot_id} erfolgreich abgeerntet.")
                except UpstreamAPIError as e:
                    logger.warning(
                        f"Biosprit-Anlage: Fehler beim Ernten von Slot {slot.slot_id}: {e}"
                    )

        return harvested_count

    async def refill(self, stock_service: StockService) -> int:
        """Insert surplus crops into unlocked empty slots to start Biosprit production."""
        if not self.config.auto_refill:
            return 0

        refilled_count = 0
        for slot in self.slots.values():
            if not slot.is_waiting_for_refill:
                continue

            logger.info(
                f"Biosprit-Anlage: Slot {slot.slot_id} (Level {slot.level}) wartet auf Befüllung "
                f"({slot.current_points}/{slot.production_limit} Punkte, noch {slot.points_needed} benötigt)..."
            )

            # Determine best products to insert for this specific slot:
            # 1. Slot-specific preference if configured
            slot_pids = self.config.slot_preferred_pids.get(slot.slot_id, [])
            candidates = [pid for pid in slot_pids if pid in slot.accepted_products]

            # 2. General preferred_pids configured by user
            for pid in self.config.preferred_pids:
                if pid in slot.accepted_products and pid not in candidates:
                    candidates.append(pid)

            # 3. Secondary fallback: all other accepted products
            for pid in slot.accepted_products:
                if pid not in candidates:
                    candidates.append(pid)

            for pid in candidates:
                if slot.points_needed <= 0:
                    break

                pts_per_unit = slot.accepted_products[pid]
                product = stock_service.get_product(pid)
                if not product:
                    continue

                available = product.total_amount - self.config.min_reserve
                if available <= 0:
                    continue

                needed_units = math.ceil(slot.points_needed / pts_per_unit)
                to_insert = min(available, needed_units)

                if to_insert <= 0:
                    continue

                pts_to_add = to_insert * pts_per_unit
                logger.info(
                    f"Biosprit-Anlage: Werfe {to_insert}x '{product.name}' (PID {pid}) in Slot {slot.slot_id} ein "
                    f"({pts_to_add} Punkte, Ziel: {slot.production_limit})..."
                )

                try:
                    res = await self.client.api_call(
                        "farm",
                        {
                            "mode": "fuelstation_entry",
                            "farm": self.farm_id,
                            "position": self.position,
                            "slot": slot.slot_id,
                            "pid": pid,
                            "amount": to_insert,
                        },
                    )
                    datablock = res.get("datablock")
                    is_ok = (
                        (isinstance(datablock, (list, tuple)) and len(datablock) > 0 and datablock[0] == 1)
                        or datablock == 1
                        or bool(res.get("updateblock"))
                    )
                    if is_ok:
                        stock_service.update(res)
                        slot.current_points += pts_to_add
                        if slot.points_needed <= 0:
                            slot.busy = True
                            refilled_count += 1
                            logger.info(
                                f"Biosprit-Anlage: Slot {slot.slot_id} vollständig befüllt "
                                f"({slot.current_points}/{slot.production_limit} Punkte) und gestartet!"
                            )
                            break
                        else:
                            logger.info(
                                f"Biosprit-Anlage: Slot {slot.slot_id} Teilbefüllung: "
                                f"{slot.current_points}/{slot.production_limit} Punkte "
                                f"(noch {slot.points_needed} benötigt)."
                            )
                except UpstreamAPIError as e:
                    logger.warning(
                        f"Biosprit-Anlage: Fehler beim Einwurf von {to_insert}x PID {pid} in Slot {slot.slot_id}: {e}"
                    )

        return refilled_count

    async def serve(self, stock_service: StockService) -> dict[str, int]:
        """Execute full Biosprit-Anlage cycle (harvest & refill)."""
        if not self.config.enabled:
            return {"harvested": 0, "refilled": 0}

        harvested = await self.harvest()
        refilled = await self.refill(stock_service)
        return {"harvested": harvested, "refilled": refilled}
