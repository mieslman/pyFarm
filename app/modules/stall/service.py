from datetime import datetime
from typing import Any, Optional
from loguru import logger

from app.config import settings
from app.core.client import MFFGameClient
from app.modules.stall.models import (
    MarketStall,
    StallSnapshot,
    StallSlot,
    StallSummary,
)


class FruitStallService:
    """Manages the Obststand / Marktbuden (fruit market stalls) in Klein Muhstein and Teichlingen."""

    def __init__(self, client: MFFGameClient):
        self.client = client
        self.snapshot: Optional[StallSnapshot] = None

    async def init_remote(self, stock_service: Optional[Any] = None) -> Optional[StallSnapshot]:
        """Fetch market stalls state from game server and construct snapshot."""
        try:
            res = await self.client.api_call("farm", {"mode": "stall_init"})
            stall_container = res.get("updateblock", {}).get("map", {}).get("stall", {})
            if not stall_container or "data" not in stall_container:
                logger.debug("FruitStallService: Keine Marktbuden auf der Karte vorhanden.")
                return None

            data = stall_container.get("data", {})
            config = stall_container.get("config", {})
            lvl_config = config.get("level", {})

            stalls_map: dict[str, MarketStall] = {}

            for pos_key, s_info in data.items():
                pos_str = str(pos_key)
                level = int(s_info.get("level", 1))

                # Lookup capacity (fillsum) for this stall and level
                fillsum = 100
                pos_levels = lvl_config.get(pos_str, {})
                if str(level) in pos_levels:
                    fillsum = int(pos_levels[str(level)].get("fillsum", 100))

                reward_val = s_info.get("reward")
                reward_ready = bool(reward_val)

                raw_slots = s_info.get("slots", {})
                slots_map: dict[str, StallSlot] = {}

                for slot_key, slot_val in raw_slots.items():
                    s_id = str(slot_key)
                    pid: Optional[int] = None
                    amount = 0
                    time_val: Optional[int] = None

                    if isinstance(slot_val, dict):
                        raw_pid = slot_val.get("pid")
                        if raw_pid is not None and str(raw_pid).isdigit():
                            pid = int(raw_pid)
                        amount = int(slot_val.get("amount", 0))
                        raw_time = slot_val.get("time")
                        if raw_time is not None and str(raw_time).isdigit():
                            time_val = int(raw_time)

                    p_name = "Leer"
                    if pid:
                        p_name = f"PID {pid}"
                        if stock_service and hasattr(stock_service, "get_product"):
                            prod = stock_service.get_product(pid)
                            if prod:
                                p_name = prod.name

                    slots_map[s_id] = StallSlot(
                        slot_id=s_id,
                        pid=pid,
                        product_name=p_name,
                        amount=amount,
                        time=time_val,
                    )

                stalls_map[pos_str] = MarketStall(
                    position=pos_str,
                    level=level,
                    fillsum=fillsum,
                    reward_ready=reward_ready,
                    points=int(s_info.get("points", 0)),
                    farmi_count=int(s_info.get("farmi_count", 0)),
                    slots=slots_map,
                )

            snapshot = StallSnapshot(
                stalls=stalls_map,
                last_updated=datetime.now().isoformat(),
            )
            self.snapshot = snapshot
            return snapshot

        except Exception as e:
            logger.warning(f"FruitStallService: Fehler beim Abruf von stall_init: {e}")
            return None

    async def collect_rewards(self) -> int:
        """Collect rewards from all stalls that have completed sales or rewards waiting."""
        if not self.snapshot:
            return 0

        collected_count = 0
        for stall in self.snapshot.stalls.values():
            if not stall.reward_ready:
                continue

            logger.info(f"FruitStallService: Hole Belohnung für Stand #{stall.position} ab...")
            try:
                res = await self.client.api_call(
                    "farm",
                    {
                        "mode": "stall_get_reward",
                        "position": stall.position,
                    },
                )
                datablock = res.get("datablock")
                # datablock can be {'reward': ...} or list/int
                if datablock:
                    stall.reward_ready = False
                    collected_count += 1
                    logger.info(f"FruitStallService: Belohnung für Stand #{stall.position} erfolgreich abgeholt.")
                else:
                    logger.warning(f"FruitStallService: Unerwartete Antwort beim Belohnung-Abholen: {res}")
            except Exception as e:
                logger.error(f"FruitStallService: Fehler beim Abholen der Belohnung für Stand #{stall.position}: {e}")

        return collected_count

    async def clear_depleted_slots(self, stock_service: Optional[Any] = None) -> int:
        """Clear slots whose fruit quantity dropped below the configured threshold percentage."""
        if not self.snapshot:
            return 0

        threshold = settings.stall.clear_threshold_percent
        cleared_count = 0

        for stall in self.snapshot.stalls.values():
            for slot in stall.slots.values():
                if slot.pid is None:
                    continue

                # Check if amount dropped below threshold
                if slot.amount < stall.fillsum * threshold:
                    logger.info(
                        f"FruitStallService: Räume Slot {slot.slot_id} an Stand #{stall.position} "
                        f"('{slot.product_name}', Restmenge: {slot.amount}/{stall.fillsum} < {threshold * 100:.0f}%)..."
                    )
                    try:
                        res = await self.client.api_call(
                            "farm",
                            {
                                "mode": "stall_clear_slot",
                                "position": stall.position,
                                "slot": slot.slot_id,
                            },
                        )
                        datablock = res.get("datablock")
                        if datablock == 1 or (isinstance(datablock, list) and datablock and datablock[0] == 1):
                            slot.pid = None
                            slot.amount = 0
                            slot.product_name = "Leer"
                            cleared_count += 1
                            logger.info(
                                f"FruitStallService: Slot {slot.slot_id} an Stand #{stall.position} erfolgreich geräumt."
                            )
                        else:
                            logger.warning(f"FruitStallService: Fehler beim Räumen von Slot {slot.slot_id}: {res}")
                    except Exception as e:
                        logger.error(f"FruitStallService: Fehler beim Räumen von Slot {slot.slot_id}: {e}")

        return cleared_count

    async def fill_free_slots(self, stock_service: Optional[Any] = None) -> int:
        """Fill empty display slots with available exotic fruits (category 'ex') from warehouse stock."""
        if not self.snapshot or not stock_service:
            return 0

        reserve = settings.stall.min_stock_reserve
        filled_count = 0

        for stall in self.snapshot.stalls.values():
            free_slots = [slot for slot in stall.slots.values() if slot.is_empty]
            if not free_slots:
                continue

            # Identify fruits currently active in this stall to avoid duplicates
            fruits_in_stall = {slot.pid for slot in stall.slots.values() if slot.pid is not None}

            # Find candidate fruits of category 'ex' with sufficient available stock above reserve
            candidates: list[Any] = []
            if hasattr(stock_service, "products"):
                for prod in stock_service.products.values():
                    if prod.category != "ex":
                        continue
                    if prod.pid in fruits_in_stall:
                        continue

                    available = stock_service.get_stock(prod.pid) if hasattr(stock_service, "get_stock") else prod.amount
                    usable = max(0, available - reserve)
                    if usable >= stall.fillsum:
                        candidates.append((usable, prod))

            # Sort candidates descending by usable amount (highest warehouse inventory first)
            candidates.sort(key=lambda x: x[0], reverse=True)

            for slot in free_slots:
                if not candidates:
                    logger.debug(
                        f"FruitStallService: Keine weiteren Exotenfrüchte mit >={stall.fillsum} Stück "
                        f"über der Reserve von {reserve} für Stand #{stall.position} verfügbar."
                    )
                    break

                _, chosen_prod = candidates.pop(0)
                logger.info(
                    f"FruitStallService: Bestücke Stand #{stall.position} Slot {slot.slot_id} "
                    f"mit {stall.fillsum}x '{chosen_prod.name}' (PID {chosen_prod.pid})..."
                )

                try:
                    res = await self.client.api_call(
                        "farm",
                        {
                            "mode": "stall_fill_slot",
                            "position": stall.position,
                            "slot": slot.slot_id,
                            "pid": chosen_prod.pid,
                            "amount": stall.fillsum,
                        },
                    )
                    datablock = res.get("datablock")
                    if datablock == 1 or (isinstance(datablock, list) and datablock and datablock[0] == 1):
                        slot.pid = chosen_prod.pid
                        slot.amount = stall.fillsum
                        slot.product_name = chosen_prod.name
                        fruits_in_stall.add(chosen_prod.pid)
                        filled_count += 1
                        logger.info(
                            f"FruitStallService: Stand #{stall.position} Slot {slot.slot_id} erfolgreich befüllt."
                        )

                        # Deduct from stock_service
                        if hasattr(stock_service, "deduct_stock"):
                            stock_service.deduct_stock(chosen_prod.pid, stall.fillsum, farm_id=1)
                    else:
                        logger.warning(
                            f"FruitStallService: Fehler beim Befüllen von Stand #{stall.position} Slot {slot.slot_id}: {res}"
                        )
                except Exception as e:
                    logger.error(
                        f"FruitStallService: Fehler beim Befüllen von Stand #{stall.position} Slot {slot.slot_id}: {e}"
                    )

        return filled_count

    async def serve(self, stock_service: Optional[Any] = None) -> dict[str, Any]:
        """Execute full fruit stall maintenance cycle: snapshot -> rewards -> clearing -> replenishment."""
        if not settings.stall.enabled:
            logger.debug("FruitStallService: Modul ist in Konfiguration deaktiviert.")
            return {"active": False, "enabled": False}

        logger.info("========== Obststand: Starte Bewirtschaftungszyklus ==========")
        snapshot = await self.init_remote(stock_service=stock_service)
        if not snapshot:
            logger.info("========== Obststand: Keine aktiven Stände gefunden ==========")
            return {"active": False}

        rewards_collected = 0
        if settings.stall.auto_collect_reward:
            rewards_collected = await self.collect_rewards()

        cleared_slots = 0
        if settings.stall.auto_clear_depleted:
            cleared_slots = await self.clear_depleted_slots(stock_service=stock_service)

        filled_slots = 0
        if settings.stall.auto_fill_slots:
            filled_slots = await self.fill_free_slots(stock_service=stock_service)

        logger.info(
            f"========== Obststand: Zyklus beendet ({len(snapshot.stalls)} Stände, "
            f"Belohnungen: {rewards_collected}, Geräumt: {cleared_slots}, Befüllt: {filled_slots}) =========="
        )

        return {
            "active": True,
            "stalls_count": len(snapshot.stalls),
            "rewards_collected": rewards_collected,
            "cleared_slots": cleared_slots,
            "filled_slots": filled_slots,
        }

    def get_summary(self) -> StallSummary:
        """Return compact summary for REST API and dashboard."""
        if not self.snapshot or not self.snapshot.stalls:
            return StallSummary(active=False)

        total_slots = sum(stall.slots_count for stall in self.snapshot.stalls.values())
        filled_slots = sum(stall.filled_slots_count for stall in self.snapshot.stalls.values())
        rewards_ready = sum(1 for stall in self.snapshot.stalls.values() if stall.reward_ready)

        return StallSummary(
            active=True,
            stalls_count=len(self.snapshot.stalls),
            total_slots=total_slots,
            filled_slots=filled_slots,
            rewards_ready_count=rewards_ready,
            last_updated=self.snapshot.last_updated,
        )
