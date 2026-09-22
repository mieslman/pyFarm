import re
from typing import Any

from loguru import logger

from app.core.client import MFFGameClient
from app.modules.sushibar.models import SushiQuestTarget, SushiRecipe
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
        except Exception as e:  # noqa: BLE001
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
        logger.info(f"SushiQuestSolver: {len(catalog)} Quests für Questreihe 5 erfolgreich geladen.")
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
    ) -> tuple[SushiRecipe | None, SushiQuestTarget | None]:
        """Find the next recipe to cook based on Questreihe 5 demand, fallback to balanced/preferred."""
        # 1. Questreihe 5 Strategy
        if strategy == "quest5":
            quests_catalog = await self.load_quests5_catalog()
            if quests_catalog:
                sorted_quests = sorted([q for q in quests_catalog if q >= current_quest_id])
                for q_id in sorted_quests:
                    reqs = quests_catalog[q_id]
                    for pid, needed_amt in reqs.items():
                        if pid not in recipes:
                            continue
                        recipe = recipes[pid]

                        # Coin protection: strictly exclude recipes costing coins
                        if recipe.is_coin_recipe:
                            continue

                        # Check level requirement
                        if recipe.level > sushibar_level:
                            continue

                        stock = stock_service.get_total_stock(pid)
                        if stock < needed_amt:
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
                                amount_in_stock=stock,
                                missing=needed_amt - stock,
                                category=recipe.category,
                            )
                            self.last_target = target

                            # Can we cook this target recipe right now?
                            if self.check_ingredients(
                                recipe,
                                stock_service,
                                catalog,
                                reserve_full_field,
                                farm_id=self.farm_id,
                            ):
                                return recipe, target
                            # If ingredients are lacking, check the next sushi requirement

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

        # 3. Balanced Fallback (cook available kT recipe with lowest warehouse stock)
        available_recipes: list[tuple[int, SushiRecipe]] = []
        for pid, recipe in recipes.items():
            if recipe.is_coin_recipe or recipe.level > sushibar_level:
                continue
            if self.check_ingredients(
                recipe, stock_service, catalog, reserve_full_field, farm_id=self.farm_id
            ):
                stock = stock_service.get_total_stock(pid)
                available_recipes.append((stock, recipe))

        if available_recipes:
            # Sort by lowest stock first
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

        sorted_quests = sorted([q for q in quests_catalog if q >= current_quest_id])
        for q_id in sorted_quests:
            reqs = quests_catalog[q_id]

            # 1. Direct water plant requirements
            for pid, needed_amt in reqs.items():
                is_water = 950 <= pid <= 957 or (
                    catalog and pid in catalog and getattr(catalog[pid], "category", "") == "water"
                )
                if is_water and pid not in seen_pids:
                    main_stock = stock_service.get_amount(pid)
                    if for_logistics:
                        # Hauptfarm (Farm 1) needs delivery if main_stock < needed_amt
                        if main_stock < needed_amt:
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
                        farm_stock = stock_service.get_farm_amount(self.farm_id, pid)
                        total_stock = farm_stock + main_stock
                        if total_stock < needed_amt + min_products:
                            deficit = (needed_amt + min_products) - total_stock
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

            # 2. Ingredients for required sushi dishes (only relevant for planting on Farm 8)
            if not for_logistics:
                for pid, needed_amt in reqs.items():
                    if pid in recipes:
                        recipe = recipes[pid]
                        if recipe.is_coin_recipe:
                            continue

                        dish_stock = stock_service.get_total_stock(pid)
                        if dish_stock < needed_amt:
                            missing_dishes = needed_amt - dish_stock
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
                                    current_water = stock_service.get_farm_amount(self.farm_id, ingr_pid)

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
