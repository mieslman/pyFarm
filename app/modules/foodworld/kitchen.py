"""Kitchen service for picking up finished dishes and starting cooking in Foodworld."""

from loguru import logger

from app.core.client import MFFGameClient
from app.modules.foodworld.models import FoodworldBuilding, FoodworldRecipe, FoodworldSlot
from app.services.stock_service import StockService


class KitchenService:
    """Manages the 4 kitchen stations (Getränke, Imbiss, Konditorei, Eisdiele)."""

    def __init__(self, client: MFFGameClient, stock_service: StockService | None = None) -> None:
        self.client = client
        self.stock_service = stock_service
        self.buildings: dict[int, FoodworldBuilding] = {}
        self.recipes: dict[int, FoodworldRecipe] = {}

    def update(self, datablock: dict) -> None:
        """Parse kitchen buildings, slots, and recipes from upstream datablock."""
        # 1. Parse buildings & slots
        raw_buildings = datablock.get("buildings", {})
        parsed_buildings: dict[int, FoodworldBuilding] = {}

        if isinstance(raw_buildings, dict):
            for b_id_str, b_data in raw_buildings.items():
                try:
                    b_id = int(b_id_str)
                    b_name = b_data.get("name", f"Küche #{b_id}")
                    level = int(b_data.get("level", 1) or 1)

                    slots: dict[int, FoodworldSlot] = {}
                    raw_slots = b_data.get("slots", {})
                    if isinstance(raw_slots, dict):
                        slots_items = list(raw_slots.items())
                    elif isinstance(raw_slots, list):
                        slots_items = [(str(idx + 1), item) for idx, item in enumerate(raw_slots)]
                    else:
                        slots_items = []

                    for s_id_str, s_data in slots_items:
                        s_id = int(s_id_str)
                        if not isinstance(s_data, dict):
                            # In PHP/JSON, empty array encodes as []
                            slot = FoodworldSlot(slot_id=s_id, pid=None, remain=0, ready=0)
                            slots[s_id] = slot
                            continue

                        pid_val = s_data.get("pid")
                        pid = (
                            int(pid_val) if pid_val is not None and str(pid_val).isdigit() else None
                        )
                        slot = FoodworldSlot(
                            slot_id=s_id,
                            pid=pid,
                            amount=int(s_data.get("amount", 0) or 0),
                            remain=int(s_data.get("remain", 0) or 0),
                            ready=int(s_data.get("ready", 0) or 0),
                            cost=int(s_data.get("cost", 0) or 0),
                            coins=int(s_data.get("coins", 0) or 0),
                            block=int(s_data.get("block", 0) or 0),
                        )
                        slots[s_id] = slot

                    parsed_buildings[b_id] = FoodworldBuilding(
                        id=b_id,
                        name=b_name,
                        level=level,
                        slots=slots,
                    )
                except (ValueError, TypeError) as e:
                    logger.debug(f"KitchenService: Fehler beim Parsen von Gebäude {b_id_str}: {e}")

        self.buildings = parsed_buildings

        # 2. Parse recipes
        raw_products = datablock.get("products", {})
        parsed_recipes: dict[int, FoodworldRecipe] = {}

        if isinstance(raw_products, dict):
            for r_id_str, r_data in raw_products.items():
                try:
                    r_id = int(r_id_str)
                    b_id = int(r_data.get("pos", 0) or 0)
                    out_dict = r_data.get("out", {})
                    if not out_dict:
                        continue
                    out_pid = int(next(iter(out_dict.keys())))
                    out_amt = int(out_dict[str(out_pid)])

                    in_dict = r_data.get("in", {})
                    reqs = {int(k): int(v) for k, v in in_dict.items() if str(k).isdigit()}

                    parsed_recipes[r_id] = FoodworldRecipe(
                        id=r_id,
                        building_id=b_id,
                        output_pid=out_pid,
                        output_amount=out_amt,
                        requirements=reqs,
                        star=int(r_data.get("star", 0) or 0),
                    )
                except (ValueError, TypeError) as e:
                    logger.debug(f"KitchenService: Fehler beim Parsen von Rezept {r_id_str}: {e}")

        self.recipes = parsed_recipes
        logger.debug(
            f"KitchenService: {len(self.buildings)} Küchen und {len(self.recipes)} Rezepte aktualisiert."
        )

    async def pickup_products(self) -> int:
        """Collect all finished dishes from kitchen slots."""
        picked_count = 0
        for building in self.buildings.values():
            for slot in building.ready_slots:
                pname = (
                    self.stock_service.products[slot.pid].name
                    if self.stock_service and slot.pid in self.stock_service.products
                    else f"PID {slot.pid}"
                )
                logger.info(
                    f"Foodworld-Küche: Hole fertiges Gericht '{pname}' aus {building.name}, Slot #{slot.slot_id} ab..."
                )
                try:
                    res = await self.client.api_call(
                        "foodworld",
                        {
                            "action": "crop",
                            "id": 0,
                            "table": building.id,
                            "chair": slot.slot_id,
                        },
                    )
                    picked_count += 1
                    slot.pid = None
                    slot.ready = 0
                    slot.remain = 0
                    if "updateblock" in res and self.stock_service:
                        self.stock_service.update(res)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"Foodworld-Küche: Fehler beim Abholen aus {building.name}, Slot #{slot.slot_id}: {e}"
                    )

        return picked_count

    async def produce(
        self,
        demanded_cart: dict[int, int] | None = None,
        demanded_pids: list[int] | None = None,
        reserve_buffer: int = 50,
        auto_buy_ingredients: bool = True,
    ) -> int:
        """Cook dishes in available, unblocked slots prioritizing waiting Farmi customer demands.

        Priority 1: Dishes demanded by waiting Farmis where net deficit > 0:
                    net_deficit = demanded - (stock + in_production)
                    Multiple slots may be used if deficit requires multiple batches.
                    Missing ingredients are automatically purchased via StockService.grasp_products
                    if auto_buy_ingredients is enabled.
        Priority 2: Maintain reserve buffer (e.g. 50), executed ONLY when no unsatisfied Farmi
                    deficits remain. Max 1 active slot per building for buffer dishes.
        Priority 3: Blind/arbitrary cooking of unrequested dishes is disabled.
        """
        started_count = 0

        # Build demands mapping {pid: total_quantity}
        demands: dict[int, int] = {}
        if demanded_cart:
            demands.update(demanded_cart)
        elif demanded_pids:
            for pid in demanded_pids:
                demands[pid] = demands.get(pid, 0) + 1

        # Calculate in-flight production across all kitchen buildings
        in_production: dict[int, int] = {}
        for b in self.buildings.values():
            for s in b.slots.values():
                if s.pid is not None and not s.is_free:
                    amt = s.amount if s.amount > 0 else 1
                    in_production[s.pid] = in_production.get(s.pid, 0) + amt

        # Calculate net deficit for each demanded dish
        net_deficit: dict[int, int] = {}
        for pid, needed_amt in demands.items():
            current_stock = self.stock_service.get_amount(pid) if self.stock_service else 0
            in_prog = in_production.get(pid, 0)
            deficit = max(0, needed_amt - (current_stock + in_prog))
            if deficit > 0:
                net_deficit[pid] = deficit

        # Track ingredient consumption within this cycle to avoid overcommitting
        used_ingredients: dict[int, int] = {}
        failed_recipes: set[int] = set()

        def has_available_ingredients(recipe: FoodworldRecipe) -> bool:
            if not self.stock_service:
                return True
            for ing_pid, needed_amt in recipe.requirements.items():
                available = (
                    self.stock_service.get_amount(ing_pid) - used_ingredients.get(ing_pid, 0)
                )
                if available < needed_amt:
                    return False
            return True

        for building in self.buildings.values():
            free_slots = building.free_slots
            if not free_slots:
                continue

            # Recipes belonging to this building
            b_recipes = [r for r in self.recipes.values() if r.building_id == building.id]
            if not b_recipes:
                continue

            # Already active PIDs in this building (used to prevent duplicate buffer batches)
            active_pids = {
                s.pid for s in building.slots.values() if s.pid is not None and not s.is_free
            }

            for slot in free_slots:
                candidate: FoodworldRecipe | None = None

                # 1. First priority: demanded by waiting farmis with positive net deficit
                # Pass 1a: Recipes that already have all ingredients in stock
                for pid in demands:
                    if net_deficit.get(pid, 0) <= 0:
                        continue
                    matching_recipes = [
                        r for r in b_recipes if r.output_pid == pid and r.id not in failed_recipes
                    ]
                    for r in matching_recipes:
                        if has_available_ingredients(r):
                            candidate = r
                            break
                    if candidate:
                        break

                # Pass 1b: If auto_buy_ingredients is enabled, consider recipes where ingredients can be purchased
                if not candidate and auto_buy_ingredients:
                    for pid in demands:
                        if net_deficit.get(pid, 0) <= 0:
                            continue
                        matching_recipes = [
                            r
                            for r in b_recipes
                            if r.output_pid == pid and r.id not in failed_recipes
                        ]
                        if matching_recipes:
                            candidate = matching_recipes[0]
                            break

                # 2. Second priority: below reserve buffer (only if no open Farmi deficits exist)
                has_open_farmi_deficit = any(d > 0 for d in net_deficit.values())
                if not candidate and not has_open_farmi_deficit and reserve_buffer > 0:
                    for recipe in b_recipes:
                        if recipe.id in failed_recipes or recipe.output_pid in active_pids:
                            continue
                        current_stock = (
                            self.stock_service.get_amount(recipe.output_pid)
                            if self.stock_service
                            else 0
                        )
                        total_available = current_stock + in_production.get(recipe.output_pid, 0)
                        if total_available < reserve_buffer and has_available_ingredients(recipe):
                            candidate = recipe
                            break

                # No candidate found (or no further demands/buffer needed)
                if not candidate:
                    break

                pname = (
                    self.stock_service.products[candidate.output_pid].name
                    if self.stock_service and candidate.output_pid in self.stock_service.products
                    else f"PID {candidate.output_pid}"
                )

                # Check if missing ingredients need to be purchased/grasped
                if not has_available_ingredients(candidate):
                    if (
                        not auto_buy_ingredients
                        or not self.stock_service
                        or not hasattr(self.stock_service, "grasp_products")
                    ):
                        logger.debug(
                            f"Foodworld-Küche: Überspringe '{pname}', da Zutaten fehlen und Zukauf deaktiviert ist."
                        )
                        failed_recipes.add(candidate.id)
                        continue

                    req_list = [
                        {"pid": ing_pid, "amount": req_amt + used_ingredients.get(ing_pid, 0)}
                        for ing_pid, req_amt in candidate.requirements.items()
                    ]
                    logger.info(
                        f"Foodworld-Küche: Beschaffe fehlende Zutaten für '{pname}' ({candidate.requirements})..."
                    )
                    try:
                        grasped = await self.stock_service.grasp_products(req_list)
                    except Exception as e:
                        logger.warning(
                            f"Foodworld-Küche: Fehler beim Zukauf von Zutaten für '{pname}': {e}"
                        )
                        grasped = False

                    if not grasped:
                        logger.warning(
                            f"Foodworld-Küche: Zutaten für '{pname}' konnten nicht beschafft werden (Grasping fehlgeschlagen)."
                        )
                        failed_recipes.add(candidate.id)
                        continue

                logger.info(
                    f"Foodworld-Küche: Starte Zubereitung von '{pname}' in {building.name}, Slot #{slot.slot_id}..."
                )
                try:
                    res = await self.client.api_call(
                        "foodworld",
                        {
                            "action": "production",
                            "id": candidate.id,
                            "table": building.id,
                            "chair": slot.slot_id,
                        },
                    )
                    started_count += 1
                    slot.pid = candidate.output_pid
                    slot.ready = 0
                    slot.remain = 7200  # Initial placeholder
                    active_pids.add(candidate.output_pid)

                    # Deduct ingredients in simulated tracker
                    for ing_pid, req_amt in candidate.requirements.items():
                        used_ingredients[ing_pid] = (
                            used_ingredients.get(ing_pid, 0) + req_amt
                        )

                    # Update in-production and net deficit
                    produced_amt = candidate.output_amount if candidate.output_amount > 0 else 1
                    in_production[candidate.output_pid] = (
                        in_production.get(candidate.output_pid, 0) + produced_amt
                    )
                    if candidate.output_pid in net_deficit:
                        net_deficit[candidate.output_pid] = max(
                            0, net_deficit[candidate.output_pid] - produced_amt
                        )

                    if "updateblock" in res and self.stock_service:
                        self.stock_service.update(res)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"Foodworld-Küche: Fehler beim Starten von '{pname}' in {building.name}: {e}"
                    )
                    failed_recipes.add(candidate.id)

        return started_count

    def _has_ingredients(self, recipe: FoodworldRecipe) -> bool:
        """Check if all required ingredient products are available in stock."""
        if not self.stock_service:
            return True

        for ing_pid, needed_amt in recipe.requirements.items():
            if self.stock_service.get_amount(ing_pid) < needed_amt:
                return False
        return True
