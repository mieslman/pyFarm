from typing import Any

from loguru import logger

from app.core.client import MFFGameClient
from app.modules.sushibar.models import SushiProductionSlot, SushiRecipe
from app.modules.sushibar.solver import SushiQuestSolver
from app.services.stock_service import StockService


class SushiKitchenService:
    """Manages cooking and harvesting in the 4 Sushi-Bar kitchen slots."""

    def __init__(self, client: MFFGameClient):
        self.client = client
        self.slots: list[SushiProductionSlot] = []

    def update_slots(
        self,
        raw_production: dict[str, Any] | list[Any],
        raw_slots_cfg: dict[str, Any] | None,
        recipes: dict[int, SushiRecipe],
        catalog: dict[int, Any] | None = None,
    ):
        """Parse raw production slots from sushibar response."""
        self.slots = []
        # Upstream has slots 1..4
        prod_dict: dict[int, Any] = {}
        if isinstance(raw_production, dict):
            for k, v in raw_production.items():
                if str(k).isdigit():
                    prod_dict[int(k)] = v

        slots_cfg: dict[int, Any] = {}
        if isinstance(raw_slots_cfg, dict):
            for k, v in raw_slots_cfg.items():
                if str(k).isdigit():
                    slots_cfg[int(k)] = v

        for slot_idx in range(1, 5):
            slot_data = prod_dict.get(slot_idx)
            cfg_data = slots_cfg.get(slot_idx, {})

            # Determine lock status: Slot 1 is always unlocked. Others depend on buy_time or cfg block
            block = 0
            if slot_idx > 1:
                if cfg_data.get("buy_time"):
                    block = 0
                elif cfg_data.get("block", 1) == 1 and not slot_data:
                    block = 1

            pid = None
            prod_name = ""
            amount = 0
            startdate = None
            duration = 0
            remain = 0
            gone = 0

            if isinstance(slot_data, dict):
                gone = int(slot_data.get("gone", 0))
                duration = int(slot_data.get("duration", 0))
                inner = slot_data.get("1", {})
                if isinstance(inner, dict) and inner.get("pid"):
                    pid = int(inner.get("pid"))
                    amount = int(inner.get("amount", 0))
                    startdate = int(inner.get("startdate", 0))
                    remain = int(inner.get("remain", 0))
                    block = 0

            if pid:
                if catalog and pid in catalog:
                    prod_name = catalog[pid].name
                elif pid in recipes:
                    prod_name = recipes[pid].name

            self.slots.append(
                SushiProductionSlot(
                    slot=slot_idx,
                    pid=pid,
                    product_name=prod_name,
                    amount=amount,
                    startdate=startdate,
                    duration=duration,
                    remain=remain,
                    gone=gone,
                    block=block,
                )
            )

    async def harvest(self, update_callback: Any | None = None) -> int:
        """Harvest all completed production slots."""
        harvested_count = 0
        for slot in self.slots:
            if slot.is_ready:
                logger.info(
                    f"Sushi-Bar: Ernte {slot.amount}x {slot.product_name or f'PID {slot.pid}'} aus Kochslot {slot.slot}..."
                )
                try:
                    res = await self.client.api_call(
                        "farm",
                        {
                            "mode": "sushibar_harvestproduction",
                            "slot": slot.slot,
                            "position": 1,
                        },
                    )
                    harvested_count += 1
                    if update_callback and isinstance(res, dict):
                        await update_callback(res)
                except Exception as e:  # noqa: BLE001
                    logger.error(f"Sushi-Bar: Fehler beim Ernten von Slot {slot.slot}: {e}")

        return harvested_count

    async def produce(
        self,
        stock_service: StockService,
        quest_solver: SushiQuestSolver,
        sushibar_level: int,
        recipes: dict[int, SushiRecipe],
        catalog: dict[int, Any] | None = None,
        current_quest_id: int = 1,
        strategy: str = "quest5",
        preferred_pids: list[int] | None = None,
        reserve_full_field: bool = True,
        coin_protection: bool = True,
        update_callback: Any | None = None,
    ) -> int:
        """Start new recipes in all free unlocked slots."""
        started_count = 0
        free_slots = [s for s in self.slots if s.is_empty]
        if not free_slots:
            return 0

        for slot in free_slots:
            recipe, target = await quest_solver.find_best_recipe(
                current_quest_id=current_quest_id,
                sushibar_level=sushibar_level,
                recipes=recipes,
                stock_service=stock_service,
                catalog=catalog,
                strategy=strategy,
                preferred_pids=preferred_pids,
                reserve_full_field=reserve_full_field,
            )

            if not recipe:
                logger.debug("Sushi-Bar: Kein kochbares Rezept gefunden (Zutaten oder Level fehlen).")
                break

            if coin_protection and recipe.is_coin_recipe:
                logger.warning(f"Sushi-Bar: Coin-Schutz aktiv! Rezept {recipe.name} abgebrochen.")
                break

            target_info = (
                f"für Quest {target.quest_id} (noch {target.missing} benötigt)"
                if target
                else "für Vorrat"
            )
            logger.info(
                f"Sushi-Bar: Starte Zubereitung von {recipe.name} (PID {recipe.pid}) "
                f"in Slot {slot.slot} {target_info}..."
            )

            try:
                res = await self.client.api_call(
                    "farm",
                    {
                        "mode": "sushibar_startproduction",
                        "slot": slot.slot,
                        "pid": recipe.pid,
                    },
                )
                started_count += 1
                # Deduct ingredients locally from stock
                for ingr_pid, amount in recipe.needs.items():
                    stock_service.deduct_stock(ingr_pid, amount, farm_id=8)

                if update_callback and isinstance(res, dict):
                    await update_callback(res)
            except Exception as e:  # noqa: BLE001
                logger.error(f"Sushi-Bar: Fehler beim Starten von Slot {slot.slot}: {e}")
                break

        return started_count
