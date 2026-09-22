from typing import Any

from loguru import logger

from app.config import FactoryConfig, settings
from app.core.client import MFFGameClient
from app.core.exceptions import UpstreamAPIError
from app.models.factory import (
    FactoryBuildingData,
    FactoryIngredient,
    FactoryRecipe,
    FactorySlot,
)
from app.services.stock_service import StockService

FACTORY_NAMES: dict[int, str] = {
    7: "Mayomacher",
    8: "Käserei",
    9: "Wollspinnerei",
    10: "Bonbonküche",
    13: "Ölpresse",
    14: "Fabrik",
    16: "Strickerei",
    25: "Marmeladenküche",
}


class Factory:
    """Controls farm processing factories (e.g. Ölpresse, Käserei, Wollspinnerei, Strickerei, Marmeladenküche).

    Features:
    - Auto-harvest finished products from ready slots.
    - Recipe selection: Quests with missing items have top priority,
      otherwise crafts the product with the lowest inventory.
    - Strict local shelf isolation for specialized farms >= 5 (no Farm 1 fallback, no auto-buy).
    - Strict coin protection (never unlocks or rents slots with block: 1, ignores coin recipes).
    """

    def __init__(
        self,
        client: MFFGameClient,
        farm_id: int,
        position: int,
        building_id: int,
        name: str | None = None,
        config: FactoryConfig | None = None,
    ):
        self.client = client
        self.farm_id = farm_id
        self.position = position
        self.building_id = building_id
        self.name = name or FACTORY_NAMES.get(building_id, "Fabrik")
        self.config = config or settings.factories
        self.level: int = 1
        self.slots: dict[int, FactorySlot] = {}
        self.recipes: dict[str, FactoryRecipe] = {}

    async def update(
        self,
        body: dict[str, Any] | None = None,
        catalog: dict[int, Any] | None = None,
    ) -> bool:
        """Fetch and parse current slots, production states and available recipes."""
        if not body:
            try:
                body = await self.client.api_call(
                    "farm",
                    {
                        "mode": "innerinfos",
                        "farm": self.farm_id,
                        "position": self.position,
                    },
                )
            except UpstreamAPIError as e:
                logger.warning(
                    f"Konnte {self.name} {self.farm_id}/{self.position} nicht aktualisieren: {e}"
                )
                return False

        datablock = body.get("datablock", [])
        if len(datablock) < 2 or not isinstance(datablock[1], dict):
            logger.warning(
                f"Ungültiger Datablock für {self.name} {self.farm_id}/{self.position}: {datablock}"
            )
            return False

        data = datablock[1]
        self.level = int(data.get("level", 1))

        # 1. Parse slots
        raw_slots = data.get("slots", {})
        self.slots.clear()
        if isinstance(raw_slots, dict):
            for slot_str, s_data in raw_slots.items():
                if not str(slot_str).isdigit():
                    continue
                slot_id = int(slot_str)

                # Empty slots are sent as empty lists [] in PHP JSON encoding
                if isinstance(s_data, list):
                    self.slots[slot_id] = FactorySlot(
                        slot_id=slot_id,
                        is_blocked=False,
                        is_empty=True,
                        is_ready=False,
                    )
                elif isinstance(s_data, dict):
                    is_blocked = bool(s_data.get("block") == 1)
                    is_ready = bool(s_data.get("ready") == 1)
                    raw_pid = s_data.get("pid")
                    pid = int(raw_pid) if raw_pid else None
                    raw_amt = s_data.get("amount")
                    amount = int(raw_amt) if raw_amt else None
                    raw_rem = s_data.get("remain")
                    remain = int(raw_rem) if raw_rem is not None else None

                    is_empty = not is_blocked and pid is None and remain is None and not is_ready
                    product_name = ""
                    if pid:
                        if catalog and pid in catalog:
                            product_name = catalog[pid].name
                        else:
                            product_name = str(pid)

                    self.slots[slot_id] = FactorySlot(
                        slot_id=slot_id,
                        is_blocked=is_blocked,
                        is_empty=is_empty,
                        is_ready=is_ready,
                        pid=pid,
                        product_name=product_name,
                        amount=amount,
                        remain_seconds=remain,
                    )

        # 2. Parse recipes (products)
        raw_products = data.get("products", {})
        self.recipes.clear()
        if isinstance(raw_products, dict):
            for item_id, p_info in raw_products.items():
                # Structure: [unlock_req, [[ingr_pid, ingr_amt], ...], [output_pid, output_amt], points, duration, min_level, building_id]
                if not isinstance(p_info, list) or len(p_info) < 3:
                    continue

                raw_ingredients = p_info[1] if isinstance(p_info[1], list) else []
                ingredients: list[FactoryIngredient] = []
                for ingr in raw_ingredients:
                    if isinstance(ingr, list) and len(ingr) >= 2:
                        try:
                            i_pid = int(ingr[0])
                            i_amt = int(ingr[1])
                            i_name = (
                                catalog[i_pid].name
                                if (catalog and i_pid in catalog)
                                else str(i_pid)
                            )
                            ingredients.append(
                                FactoryIngredient(pid=i_pid, amount=i_amt, name=i_name)
                            )
                        except (ValueError, TypeError):
                            continue

                raw_output = p_info[2]
                if not isinstance(raw_output, list) or len(raw_output) < 2:
                    continue
                try:
                    out_pid = int(raw_output[0])
                    out_amt = int(raw_output[1])
                except (ValueError, TypeError):
                    continue

                out_name = (
                    catalog[out_pid].name
                    if (catalog and out_pid in catalog)
                    else str(out_pid)
                )
                points = int(p_info[3]) if len(p_info) > 3 and str(p_info[3]).isdigit() else 0
                duration = int(p_info[4]) if len(p_info) > 4 and str(p_info[4]).isdigit() else 0
                min_lvl = int(p_info[5]) if len(p_info) > 5 and str(p_info[5]).isdigit() else 0

                cost_coins = 0
                # Check for coins in index 0
                if isinstance(p_info[0], list) and len(p_info[0]) >= 2:
                    # e.g. [[coins, val], ...]
                    pass

                self.recipes[str(item_id)] = FactoryRecipe(
                    item_id=str(item_id),
                    output_pid=out_pid,
                    output_amount=out_amt,
                    name=out_name,
                    duration=duration,
                    points=points,
                    cost_coins=cost_coins,
                    min_level=min_lvl,
                    ingredients=ingredients,
                )

        return True

    async def harvest(
        self,
        slot_id: int,
        stock_service: StockService | None = None,
    ) -> bool:
        """Harvest finished product from a ready slot."""
        slot = self.slots.get(slot_id)
        prod_desc = slot.product_name if slot and slot.product_name else f"Slot {slot_id}"
        logger.info(
            f"{self.name} {self.farm_id}/{self.position}: Ernte '{prod_desc}' (Slot {slot_id})..."
        )

        try:
            res = await self.client.api_call(
                "farm",
                {
                    "mode": "harvestproduction",
                    "farm": self.farm_id,
                    "position": self.position,
                    "slot": slot_id,
                },
            )
            if stock_service and res:
                stock_service.update(res)

            if slot:
                slot.is_ready = False
                slot.is_empty = True
                slot.pid = None
                slot.amount = None
                slot.remain_seconds = None

            logger.info(
                f"{self.name} {self.farm_id}/{self.position}: Ernte für Slot {slot_id} erfolgreich."
            )
            return True
        except UpstreamAPIError as e:
            logger.error(
                f"{self.name} {self.farm_id}/{self.position}: Erntefehler für Slot {slot_id}: {e}"
            )
            return False

    def select_recipe(
        self,
        stock_service: StockService,
        quest_status_main: dict[str, Any] | None = None,
        quest_requirements: dict[int, int] | None = None,
    ) -> FactoryRecipe | None:
        """Select best recipe to produce.

        Priority 1: Active quests with missing requirements.
        Priority 2: Product with the lowest current inventory.
        Strict Constraint: For farm >= 5, ingredients MUST come only from the local farm rack.
        """
        craftable_recipes: list[FactoryRecipe] = []

        for recipe in self.recipes.values():
            if self.config.coin_protection and recipe.cost_coins > 0:
                continue

            can_craft = True
            for ingr in recipe.ingredients:
                if self.farm_id >= 5:
                    # Strict isolation: only use physical stock on this specific farm rack
                    avail = stock_service.get_farm_amount(
                        farm_id=self.farm_id,
                        pid=ingr.pid,
                        include_fallback=False,
                    )
                else:
                    # Standard farm: use central main inventory
                    avail = stock_service.get_amount(ingr.pid)

                if avail < ingr.amount:
                    can_craft = False
                    break

            if can_craft:
                craftable_recipes.append(recipe)

        if not craftable_recipes:
            return None

        # --- Priority 1: Quest requirements with missing quantities ---
        quest_deficits: dict[int, int] = {}
        if quest_requirements:
            for q_pid, q_amt in quest_requirements.items():
                cur_stock = stock_service.get_total_stock(q_pid)
                if cur_stock < q_amt:
                    quest_deficits[q_pid] = q_amt - cur_stock

        if quest_status_main:
            for q_entry in quest_status_main.values():
                if isinstance(q_entry, dict):
                    q_data = q_entry.get("data", {})
                    raw_reqs = q_data.get("1", [{}])
                    q_reqs = raw_reqs[0] if (isinstance(raw_reqs, list) and raw_reqs) else {}
                    if isinstance(q_reqs, dict):
                        for p_str, n_amt in q_reqs.items():
                            if str(p_str).isdigit():
                                p_id = int(p_str)
                                cur_stock = stock_service.get_total_stock(p_id)
                                if cur_stock < int(n_amt):
                                    quest_deficits[p_id] = max(
                                        quest_deficits.get(p_id, 0),
                                        int(n_amt) - cur_stock,
                                    )

        # Check if any craftable recipe satisfies a quest deficit
        quest_candidates: list[tuple[FactoryRecipe, int]] = []
        for rec in craftable_recipes:
            if rec.output_pid in quest_deficits and quest_deficits[rec.output_pid] > 0:
                quest_candidates.append((rec, quest_deficits[rec.output_pid]))

        if quest_candidates:
            # Sort by highest missing deficit first
            quest_candidates.sort(key=lambda x: x[1], reverse=True)
            chosen_rec, def_amt = quest_candidates[0]
            logger.info(
                f"{self.name} {self.farm_id}/{self.position}: Quest-Priorität gewählt: "
                f"'{chosen_rec.name}' (PID {chosen_rec.output_pid}, Fehlmenge: {def_amt})."
            )
            return chosen_rec

        # --- Priority 2: Lowest inventory product ---
        def get_inventory(rec: FactoryRecipe) -> int:
            if self.farm_id >= 5:
                return stock_service.get_farm_amount(
                    farm_id=self.farm_id,
                    pid=rec.output_pid,
                    include_fallback=False,
                )
            return stock_service.get_amount(rec.output_pid)

        # Sort craftable recipes ascending by current stock, then shorter duration
        craftable_recipes.sort(key=lambda r: (get_inventory(r), r.duration))
        chosen = craftable_recipes[0]
        cur_inv = get_inventory(chosen)
        logger.info(
            f"{self.name} {self.farm_id}/{self.position}: Min-Bestand gewählt: "
            f"'{chosen.name}' (PID {chosen.output_pid}, Aktueller Bestand: {cur_inv})."
        )
        return chosen

    async def produce(
        self,
        slot_id: int,
        recipe: FactoryRecipe,
        stock_service: StockService,
    ) -> bool:
        """Start production of a recipe in a specific slot."""
        logger.info(
            f"{self.name} {self.farm_id}/{self.position}: Starte Produktion von "
            f"'{recipe.name}' (Item {recipe.item_id}, {recipe.output_amount}x PID {recipe.output_pid}) in Slot {slot_id}..."
        )

        try:
            res = await self.client.api_call(
                "farm",
                {
                    "mode": "start",
                    "farm": self.farm_id,
                    "position": self.position,
                    "slot": slot_id,
                    "item": recipe.item_id,
                },
            )
            # Locally deduct ingredients from stock
            for ingr in recipe.ingredients:
                stock_service.deduct_stock(
                    pid=ingr.pid,
                    amount=ingr.amount,
                    farm_id=self.farm_id if self.farm_id >= 5 else None,
                )

            # Update slot state
            slot = self.slots.get(slot_id)
            if slot:
                slot.is_empty = False
                slot.pid = recipe.output_pid
                slot.product_name = recipe.name
                slot.amount = recipe.output_amount
                slot.remain_seconds = recipe.duration
                slot.is_ready = False

            if res:
                stock_service.update(res)

            logger.info(
                f"{self.name} {self.farm_id}/{self.position}: Produktion für Slot {slot_id} erfolgreich gestartet."
            )
            return True
        except UpstreamAPIError as e:
            logger.error(
                f"{self.name} {self.farm_id}/{self.position}: Fehler beim Starten in Slot {slot_id}: {e}"
            )
            return False

    async def serve(
        self,
        stock_service: StockService,
        quest_status_main: dict[str, Any] | None = None,
        quest_requirements: dict[int, int] | None = None,
        catalog: dict[int, Any] | None = None,
    ) -> None:
        """Complete service loop: update -> harvest ready slots -> start production in free slots."""
        if not self.config.enabled:
            return

        ok = await self.update(catalog=catalog)
        if not ok:
            return

        # 1. Ernten fertiger Slots
        if self.config.auto_harvest:
            for slot_id, slot in sorted(self.slots.items()):
                if slot.is_ready:
                    await self.harvest(slot_id, stock_service)

        # 2. Neue Produktion starten in freien, unblockierten Slots
        if self.config.auto_produce:
            for slot_id, slot in sorted(self.slots.items()):
                if not slot.is_blocked and slot.is_empty:
                    recipe = self.select_recipe(
                        stock_service=stock_service,
                        quest_status_main=quest_status_main,
                        quest_requirements=quest_requirements,
                    )
                    if recipe:
                        await self.produce(slot_id, recipe, stock_service)
                    else:
                        logger.debug(
                            f"{self.name} {self.farm_id}/{self.position} Slot {slot_id}: "
                            f"Kein herstellbares Rezept mit verfügbaren Rohstoffen."
                        )

    def to_building_data(self) -> FactoryBuildingData:
        """Export current runtime state as Pydantic DTO."""
        return FactoryBuildingData(
            farm_id=self.farm_id,
            position=self.position,
            building_id=self.building_id,
            name=self.name,
            level=self.level,
            slots=list(self.slots.values()),
            recipes=list(self.recipes.values()),
        )
