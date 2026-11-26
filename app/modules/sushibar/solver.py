import re
from typing import Any

from loguru import logger

from app.core.client import MFFGameClient
from app.modules.sushibar.models import SushiFarmi, SushiQuestTarget, SushiRecipe
from app.services.stock_service import StockService


class SushiQuestSolver:
    """Solves and prioritizes sushi recipes based on Questreihe 5 (Wasserschutzquests).

    Rules:
    1. Analyzes Questreihe 5 sequentially from the player's current quest up to Quest 100.
    2. Considers only kT-based recipes (strict coin protection: cost_coins == 0).
    3. Checks player sushibar level requirements.
    4. Evaluates ingredient availability under strict field-planting reserve:
       Retains at least 120 // (size_x * size_y) units in stock for any field crop.
    """

    def __init__(self, client: MFFGameClient | None = None, farm_id: int = 8):
        self.client = client
        self.farm_id = farm_id
        self._quests5_cache: dict[int, dict[int, int]] = {}  # quest_id -> {pid: amount}
        self.last_target: SushiQuestTarget | None = None

    async def load_quests5_catalog(self) -> dict[int, dict[int, int]]:
        """Fetch and parse all 100 quests of Hauptquestreihe 5 from help.php."""
        if self._quests5_cache:
            return self._quests5_cache

        if not self.client:
            return {}

        try:
            res = await self.client.client.post(
                f"https://s{self.client.server}.myfreefarm.de/ajax/help.php",
                data={"rid": self.client.rid, "mode": "quests5"},
            )
            if res.status_code != 200:
                logger.error(f"Konnte help.php quests5 nicht laden (HTTP {res.status_code}).")
                return {}

            data = res.json()
            if not isinstance(data, list) or len(data) < 2:
                return {}

            content = data[1].get("content", "")
            return self.parse_quests_html(content)
        except Exception as e:
            logger.opt(exception=True).error(f"Fehler beim Laden von Questreihe 5: {e}")
            return {}

    def parse_quests_html(self, html_content: str) -> dict[int, dict[int, int]]:
        """Parse HTML table rows into a dictionary: quest_nr -> {pid: amount}."""
        catalog: dict[int, dict[int, int]] = {}
        rows = re.findall(
            r'<tr class="newhelp_line">.*?<td valign="top">(\d+)\.</td>\s*<td valign="top">(.*?)</td>',
            html_content,
            re.DOTALL,
        )
        for q_nr_str, req_html in rows:
            q_nr = int(q_nr_str)
            matches = re.findall(
                r'class="kp(\d+)".*?(\d[\d\.]*)x',
                req_html,
                re.DOTALL,
            )
            requirements: dict[int, int] = {}
            for pid_s, amt_s in matches:
                pid = int(pid_s)
                amt = int(amt_s.replace(".", "").replace(",", "").strip())
                requirements[pid] = amt
            if requirements:
                catalog[q_nr] = requirements

        self._quests5_cache = catalog
        logger.info(
            f"SushiQuestSolver: {len(catalog)} Quests für Questreihe 5 erfolgreich geladen."
        )
        return catalog

    def check_ingredients(
        self,
        recipe: SushiRecipe,
        stock_service: StockService,
        catalog: dict[int, Any] | None = None,
        reserve_full_field: bool = True,
        farm_id: int | None = None,
    ) -> bool:
        """Check if all required ingredients are in stock respecting the field reserve."""
        f_id = farm_id if farm_id is not None else self.farm_id
        for ingr_pid, needed_amount in recipe.needs.items():
            stock = stock_service.get_farm_amount(f_id, ingr_pid, include_fallback=True)
            field_reserve = 0
            if reserve_full_field:
                prod = catalog.get(ingr_pid) if catalog else stock_service.get_product(ingr_pid)
                # Dynamic field planting reserve: 120 // (x * y) for plant crops
                if prod and getattr(prod, "category", "") in ("v", "water"):
                    sx = getattr(prod, "size_x", 1) or 1
                    sy = getattr(prod, "size_y", 1) or 1
                    field_reserve = 120 // (sx * sy)

            available = stock - field_reserve
            if available < needed_amount:
                logger.debug(
                    f"Zutat PID {ingr_pid} reicht nicht für {recipe.name}: "
                    f"Bestand {stock} - Reserve {field_reserve} = {available} < benötigt {needed_amount}"
                )
                return False
        return True

    async def find_best_recipe(
        self,
        current_quest_id: int,
        sushibar_level: int,
        recipes: dict[int, SushiRecipe],
        stock_service: StockService,
        catalog: dict[int, Any] | None = None,
        strategy: str = "quest5",
        preferred_pids: list[int] | None = None,
        reserve_full_field: bool = True,
        fallback_to_balanced: bool = False,
        farmis: list[SushiFarmi] | None = None,
        in_production: dict[int, int] | None = None,
        balanced_max_stock: int = 150,
    ) -> tuple[SushiRecipe | None, SushiQuestTarget | None]:
        """Find the next recipe to cook based on Questreihe 5, seated Farmi demand, or balanced stock."""
        prod_map = in_production or {}

        # 1. Questreihe 5 Strategy
        if strategy == "quest5":
            quests_catalog = await self.load_quests5_catalog()
            if quests_catalog:
                sorted_quests = sorted([q for q in quests_catalog if q >= current_quest_id])
                # Track available stock cumulatively across upcoming quests (including active kitchen slots)
                virtual_stock = {
                    pid: stock_service.get_total_stock(pid) + prod_map.get(pid, 0)
                    for pid in recipes
                }
                first_target: SushiQuestTarget | None = None

                for q_id in sorted_quests:
                    reqs = quests_catalog[q_id]
                    for pid, needed_amt in reqs.items():
                        if pid not in recipes:
                            continue
                        recipe = recipes[pid]

                        # Coin protection: strictly exclude recipes costing coins
                        if recipe.is_coin_recipe:
                            continue

                        avail = virtual_stock.get(pid, 0)
                        if avail >= needed_amt:
                            virtual_stock[pid] = avail - needed_amt
                            continue
                        else:
                            deficit = needed_amt - avail
                            virtual_stock[pid] = 0

                        prod_name = (
                            catalog[pid].name
                            if (catalog and pid in catalog)
                            else recipe.name or f"PID {pid}"
                        )
                        target = SushiQuestTarget(
                            quest_id=q_id,
                            pid=pid,
                            product_name=prod_name,
                            amount_needed=needed_amt,
                            amount_in_stock=stock_service.get_total_stock(pid),
                            missing=deficit,
                            category=recipe.category,
                        )
                        if first_target is None:
                            first_target = target

                        # Check level requirement
                        if recipe.level > sushibar_level:
                            continue

                        # Can we cook this target recipe right now?
                        if self.check_ingredients(
                            recipe,
                            stock_service,
                            catalog,
                            reserve_full_field,
                            farm_id=self.farm_id,
                        ):
                            self.last_target = target
                            return recipe, target
                        # If ingredients are lacking, check the next sushi requirement

                # Remember earliest unfulfilled target even if it could not be cooked immediately
                if first_target is not None:
                    self.last_target = first_target

                # If no Quest 5 recipe can be cooked and fallback is disabled, do not cook random dishes
                if not fallback_to_balanced:
                    return None, self.last_target

        # 2. Preferred PIDs Strategy (or fallback)
        if strategy == "preferred" and preferred_pids:
            for pid in preferred_pids:
                if pid in recipes:
                    recipe = recipes[pid]
                    if (
                        not recipe.is_coin_recipe
                        and recipe.level <= sushibar_level
                        and self.check_ingredients(
                            recipe,
                            stock_service,
                            catalog,
                            reserve_full_field,
                            farm_id=self.farm_id,
                        )
                    ):
                        return recipe, None
            if not fallback_to_balanced:
                return None, None

        # 3. Balanced / Farmi-Demand Fallback
        # Only active if strategy is balanced or fallback_to_balanced is enabled
        if strategy == "balanced" or fallback_to_balanced:
            # 3a. Prioritize categories actively needed by seated Farmis
            if farmis:
                cat_deficit: dict[str, int] = {}
                for f in farmis:
                    for cat, needed_amt in f.need.items():
                        missing = needed_amt - f.have.get(cat, 0)
                        if missing > 0:
                            cat_deficit[cat] = cat_deficit.get(cat, 0) + missing

                if cat_deficit:
                    cat_available: dict[str, int] = {}
                    for pid, recipe in recipes.items():
                        if recipe.is_coin_recipe:
                            continue
                        avail = stock_service.get_farm_amount(self.farm_id, pid) + prod_map.get(
                            pid, 0
                        )
                        cat_available[recipe.category] = (
                            cat_available.get(recipe.category, 0) + avail
                        )

                    urgent_cats: list[tuple[int, str]] = [
                        (deficit - cat_available.get(cat, 0), cat)
                        for cat, deficit in cat_deficit.items()
                        if cat_available.get(cat, 0) < deficit
                    ]
                    urgent_cats.sort(key=lambda x: -x[0])

                    for short_by, cat in urgent_cats:
                        cat_candidates: list[tuple[int, SushiRecipe]] = []
                        for pid, recipe in recipes.items():
                            if (
                                recipe.is_coin_recipe
                                or recipe.level > sushibar_level
                                or recipe.category != cat
                            ):
                                continue
                            if self.check_ingredients(
                                recipe,
                                stock_service,
                                catalog,
                                reserve_full_field,
                                farm_id=self.farm_id,
                            ):
                                eff_stock = stock_service.get_farm_amount(
                                    self.farm_id, pid
                                ) + prod_map.get(pid, 0)
                                cat_candidates.append((eff_stock, recipe))

                        if cat_candidates:
                            cat_candidates.sort(key=lambda x: x[0])
                            chosen = cat_candidates[0][1]
                            logger.info(
                                f"Sushi-Bar: Priorisiere {chosen.name} (PID {chosen.pid}) "
                                f"für akuten Farmi-Bedarf in Kategorie '{cat}' (Defizit: {short_by})."
                            )
                            return chosen, None

            # 3b. Balanced Fallback (cook available kT recipe with lowest Farm-8 stock below cap)
            available_recipes: list[tuple[int, SushiRecipe]] = []
            for pid, recipe in recipes.items():
                if recipe.is_coin_recipe or recipe.level > sushibar_level:
                    continue
                eff_stock = stock_service.get_farm_amount(self.farm_id, pid) + prod_map.get(pid, 0)
                if balanced_max_stock > 0 and eff_stock >= balanced_max_stock:
                    logger.debug(
                        f"Sushi-Bar: Überspringe {recipe.name} im Balanced-Modus "
                        f"(Bestand+Produktion {eff_stock} >= Deckel {balanced_max_stock})."
                    )
                    continue
                if self.check_ingredients(
                    recipe, stock_service, catalog, reserve_full_field, farm_id=self.farm_id
                ):
                    available_recipes.append((eff_stock, recipe))

            if available_recipes:
                # Sort by lowest effective stock first
                available_recipes.sort(key=lambda x: x[0])
                return available_recipes[0][1], None

        return None, None

    async def get_quest5_water_requirements(
        self,
        current_quest_id: int,
        recipes: dict[int, SushiRecipe],
        stock_service: StockService,
        catalog: dict[int, Any] | None = None,
        min_products: int = 500,
        for_logistics: bool = False,
    ) -> list[tuple[int, int, int, str]]:
        """Identify water plant requirements (PIDs 950-957) from Questreihe 5.

        Scans quests sequentially from current_quest_id up to Quest 100:
        - When for_logistics=True: checks what Farm 1 (Hauptfarm) needs delivered (main_stock < needed_amt).
        - When for_logistics=False: checks what Farm 8 needs to grow/plant (total_stock < needed_amt + min_products).

        Returns:
            List of tuples: (pid, missing_amount, quest_id, reason)
            Ordered chronologically by quest progression.
        """
        quests_catalog = await self.load_quests5_catalog()
        if not quests_catalog:
            return []

        candidates: list[tuple[int, int, int, str]] = []
        seen_pids: set[int] = set()

        # Track virtual stock cumulatively across upcoming quests
        virtual_water_stock: dict[int, int] = {}
        if not for_logistics:
            for p in range(950, 958):
                virtual_water_stock[p] = stock_service.get_farm_amount(
                    self.farm_id, p
                ) + stock_service.get_amount(p)

        virtual_dish_stock = {pid: stock_service.get_total_stock(pid) for pid in recipes}

        sorted_quests = sorted([q for q in quests_catalog if q >= current_quest_id])
        for q_id in sorted_quests:
            reqs = quests_catalog[q_id]

            # 1. Direct water plant requirements and sushi dishes for logistics
            for pid, needed_amt in reqs.items():
                is_water = 950 <= pid <= 957 or (
                    catalog and pid in catalog and getattr(catalog[pid], "category", "") == "water"
                )
                is_sushi_dish = (
                    pid in recipes
                    or (970 <= pid < 1000)
                    or (
                        catalog
                        and pid in catalog
                        and getattr(catalog[pid], "category", "") in ("sushi", "soup", "salad", "dessert")
                    )
                )
                if is_water or (for_logistics and is_sushi_dish):
                    main_stock = stock_service.get_amount(pid)
                    if for_logistics:
                        # Hauptfarm (Farm 1) needs delivery if main_stock < needed_amt
                        if pid not in seen_pids and main_stock < needed_amt:
                            deficit = needed_amt - main_stock
                            prod_name = (
                                catalog[pid].name if (catalog and pid in catalog) else f"PID {pid}"
                            )
                            candidates.append(
                                (
                                    pid,
                                    deficit,
                                    q_id,
                                    f"Direktbedarf Quest {q_id} ({needed_amt}x {prod_name})",
                                )
                            )
                            seen_pids.add(pid)
                    else:
                        # Farm 8 needs to plant if total system stock < needed_amt + min_products
                        total_stock = virtual_water_stock.get(pid, 0)
                        if total_stock < needed_amt + min_products:
                            if pid not in seen_pids:
                                deficit = (needed_amt + min_products) - max(0, total_stock)
                                prod_name = (
                                    catalog[pid].name if (catalog and pid in catalog) else f"PID {pid}"
                                )
                                candidates.append(
                                    (
                                        pid,
                                        deficit,
                                        q_id,
                                        f"Direktbedarf Quest {q_id} ({needed_amt}x {prod_name})",
                                    )
                                )
                                seen_pids.add(pid)
                            virtual_water_stock[pid] = 0
                        else:
                            virtual_water_stock[pid] = total_stock - needed_amt

            # 2. Ingredients for required sushi dishes (only relevant for planting on Farm 8)
            if not for_logistics:
                for pid, needed_amt in reqs.items():
                    if pid in recipes:
                        recipe = recipes[pid]
                        if recipe.is_coin_recipe:
                            continue

                        avail_dish = virtual_dish_stock.get(pid, 0)
                        if avail_dish >= needed_amt:
                            virtual_dish_stock[pid] = avail_dish - needed_amt
                            continue
                        else:
                            missing_dishes = needed_amt - avail_dish
                            virtual_dish_stock[pid] = 0

                        craft_yield = max(1, recipe.amount)
                        crafts_needed = (missing_dishes + craft_yield - 1) // craft_yield

                        for ingr_pid, per_craft in recipe.needs.items():
                            is_water = 950 <= ingr_pid <= 957 or (
                                catalog
                                and ingr_pid in catalog
                                and getattr(catalog[ingr_pid], "category", "") == "water"
                            )
                            if is_water and ingr_pid not in seen_pids:
                                needed_water = crafts_needed * per_craft
                                current_water = stock_service.get_farm_amount(
                                    self.farm_id, ingr_pid
                                )

                                # Reserve check: 120 // (sx * sy)
                                prod = (
                                    catalog.get(ingr_pid)
                                    if catalog
                                    else stock_service.get_product(ingr_pid)
                                )
                                sx = getattr(prod, "size_x", 1) or 1
                                sy = getattr(prod, "size_y", 1) or 1
                                field_reserve = 120 // (sx * sy)
                                available_water = max(0, current_water - field_reserve)

                                if available_water < needed_water + min_products:
                                    deficit = (needed_water + min_products) - available_water
                                    dish_name = recipe.name or f"PID {pid}"
                                    candidates.append(
                                        (
                                            ingr_pid,
                                            deficit,
                                            q_id,
                                            f"Zutat für {dish_name} in Quest {q_id} (noch {missing_dishes}x Speise)",
                                        )
                                    )
                                    seen_pids.add(ingr_pid)

        return candidates

    def get_sushibar_water_shortages(
        self,
        sushibar_level: int,
        recipes: dict[int, SushiRecipe],
        stock_service: StockService,
        catalog: dict[int, Any] | None = None,
        farmis: list[SushiFarmi] | None = None,
        balanced_max_stock: int = 150,
        ingredient_buffer: int = 200,
        exclude_pids: set[int] | None = None,
    ) -> list[tuple[int, int, int, str]]:
        """Identify water-plant ingredient shortages on Farm 8 for active Sushi-Bar recipes.

        Prioritizes water crops (PIDs 950-957) that currently block or starve recipes
        needed by seated Farmis or below ``balanced_max_stock``.
        Crops in ``exclude_pids`` (e.g. already growing on a Farm 8 field) are skipped
        so at most one field is dedicated to replenishing a given ingredient at a time.
        """
        if not recipes:
            return []

        excluded = exclude_pids or set()

        # Determine categories acutely needed by seated Farmis
        urgent_cats: set[str] = set()
        if farmis:
            cat_deficit: dict[str, int] = {}
            for f in farmis:
                for cat, needed_amt in f.need.items():
                    missing = needed_amt - f.have.get(cat, 0)
                    if missing > 0:
                        cat_deficit[cat] = cat_deficit.get(cat, 0) + missing

            cat_available: dict[str, int] = {}
            for pid, recipe in recipes.items():
                if recipe.is_coin_recipe:
                    continue
                cat_available[recipe.category] = cat_available.get(
                    recipe.category, 0
                ) + stock_service.get_farm_amount(self.farm_id, pid)

            for cat, deficit in cat_deficit.items():
                if cat_available.get(cat, 0) < deficit:
                    urgent_cats.add(cat)

        # Collect best priority per water ingredient PID: (tier, usable, pid, deficit, reason)
        best_by_pid: dict[int, tuple[int, int, int, int, str]] = {}

        for pid, recipe in recipes.items():
            if recipe.is_coin_recipe or recipe.level > sushibar_level:
                continue

            dish_stock = stock_service.get_farm_amount(self.farm_id, pid)
            is_urgent_farmi = recipe.category in urgent_cats
            needs_replenish = is_urgent_farmi or (
                balanced_max_stock <= 0 or dish_stock < balanced_max_stock
            )
            if not needs_replenish:
                continue

            for ingr_pid, per_craft in recipe.needs.items():
                if ingr_pid in excluded:
                    continue
                is_water = 950 <= ingr_pid <= 957 or (
                    catalog
                    and ingr_pid in catalog
                    and getattr(catalog[ingr_pid], "category", "") == "water"
                )
                if not is_water:
                    continue

                current_water = stock_service.get_farm_amount(self.farm_id, ingr_pid)
                prod = catalog.get(ingr_pid) if catalog else stock_service.get_product(ingr_pid)
                sx = getattr(prod, "size_x", 1) or 1
                sy = getattr(prod, "size_y", 1) or 1
                field_reserve = 120 // (sx * sy)
                usable = max(0, current_water - field_reserve)

                target_usable = max(per_craft, ingredient_buffer)
                if usable >= target_usable:
                    continue

                is_blocked = usable < per_craft
                if is_urgent_farmi and is_blocked:
                    tier = 0
                elif is_blocked:
                    tier = 1
                elif is_urgent_farmi:
                    tier = 2
                else:
                    tier = 3

                deficit = target_usable - usable
                dish_name = recipe.name or f"PID {pid}"
                reason = (
                    f"Sushi-Bar Zutaten-Engpass für {dish_name} "
                    f"(Bestand {current_water}, Reserve {field_reserve})"
                )
                entry = (tier, usable, ingr_pid, deficit, reason)
                prev = best_by_pid.get(ingr_pid)
                if prev is None or (entry[0], entry[1]) < (prev[0], prev[1]):
                    best_by_pid[ingr_pid] = entry

        ranked = sorted(best_by_pid.values(), key=lambda x: (x[0], x[1]))
        return [(pid, deficit, 0, reason) for _, _, pid, deficit, reason in ranked]
