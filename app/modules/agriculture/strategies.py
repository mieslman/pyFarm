from loguru import logger

from app.models.product import Product
from app.modules.spicehouse.models import SPICE_DRIED_TO_GROUND, SPICE_RAW_TO_DRIED
from app.services.stock_service import StockService


class PlantStrategySolver:
    """Calculates the best crop candidate to plant based on inventory and objectives."""

    @staticmethod
    def get_plant_min_candidates(
        stock_service: StockService,
        category: str = "v",
        min_products: int = 500,
        farm_id: int | None = None,
        exclude_pids: set[int] | None = None,
    ) -> list[Product]:
        """Rank crops by lowest available inventory to maintain balanced stock levels.

        Prioritizes crops where seeds are currently available in the farm's local rack.
        """
        excluded = exclude_pids or set()
        candidates = [
            p
            for p in stock_service.products.values()
            if p.category == category and p.total_amount < min_products and p.pid not in excluded
        ]

        has_farm_stocks = bool(
            getattr(stock_service, "farm_stocks", None)
            or getattr(stock_service, "farm_temp_stocks", None)
        )
        is_outer_farm = farm_id in (5, 6, 8, 10)

        with_seeds = [p for p in candidates if stock_service.get_farm_amount(farm_id, p.pid) > 0]
        if with_seeds:
            candidates = with_seeds
        elif is_outer_farm and has_farm_stocks:
            return []

        # Sort prioritizing available seeds first, then lowest total stock
        candidates.sort(
            key=lambda p: (
                0 if stock_service.get_farm_amount(farm_id, p.pid) > 0 else 1,
                p.total_amount,
            )
        )
        return candidates

    @staticmethod
    def get_plant_quest_candidates(
        stock_service: StockService,
        quest_requirements: dict[int, int],
        category: str = "v",
        min_products: int = 500,
        farm_id: int | None = None,
        exclude_pids: set[int] | None = None,
    ) -> list[Product]:
        """Prioritize crops required to complete active quests.

        Prioritizes crops with available seeds in the farm's local rack.
        """
        excluded = exclude_pids or set()
        quest_candidates = []
        for pid, required_amount in quest_requirements.items():
            if pid in excluded:
                continue
            product = stock_service.get_product(pid)
            if (
                product
                and product.category == category
                and product.total_amount < (required_amount + min_products)
            ):
                quest_candidates.append(product)

        has_farm_stocks = bool(
            getattr(stock_service, "farm_stocks", None)
            or getattr(stock_service, "farm_temp_stocks", None)
        )
        is_outer_farm = farm_id in (5, 6, 8, 10)

        with_seeds = [
            p for p in quest_candidates if stock_service.get_farm_amount(farm_id, p.pid) > 0
        ]
        if with_seeds:
            quest_candidates = with_seeds
        elif is_outer_farm and has_farm_stocks:
            return PlantStrategySolver.get_plant_min_candidates(
                stock_service,
                category=category,
                min_products=min_products,
                farm_id=farm_id,
                exclude_pids=excluded,
            )

        # Sort by available seeds first, then greatest deficit
        quest_candidates.sort(
            key=lambda p: (
                0 if stock_service.get_farm_amount(farm_id, p.pid) > 0 else 1,
                -((quest_requirements.get(p.pid, 0) + min_products) - p.total_amount),
            )
        )

        # Fallback to general min stock if no quest crops are deficient
        if not quest_candidates:
            return PlantStrategySolver.get_plant_min_candidates(
                stock_service,
                category=category,
                min_products=min_products,
                farm_id=farm_id,
                exclude_pids=excluded,
            )

        return quest_candidates

    @staticmethod
    def get_farm10_balanced_spice_candidates(
        stock_service: StockService,
        min_products: int = 500,
        farm_id: int = 10,
        exclude_pids: set[int] | None = None,
    ) -> list[Product]:
        """Balance spice crops on Farm 10 using Least-Stock-First across the full chain
        (raw + dried + ground).

        Priority 1: Protect local raw spice reserve (< min_products).
        Priority 2: Plant the spice whose total chain stock (raw + dried + ground) is lowest.
        """
        excluded = exclude_pids or set()
        candidates = [
            p
            for p in stock_service.products.values()
            if p.category == "spice" and p.pid not in excluded
        ]

        has_farm_stocks = bool(
            getattr(stock_service, "farm_stocks", None)
            or getattr(stock_service, "farm_temp_stocks", None)
        )
        with_seeds = [p for p in candidates if stock_service.get_farm_amount(farm_id, p.pid) > 0]
        if with_seeds:
            candidates = with_seeds
        elif has_farm_stocks:
            return []

        has_farm1_stocks = bool(
            getattr(stock_service, "farm_stocks", None) and 1 in stock_service.farm_stocks
        )

        def _spice_sort_key(p: Product) -> tuple[int, int, int, int]:
            raw_stock = stock_service.get_farm_amount(farm_id, p.pid)
            dried_pid = SPICE_RAW_TO_DRIED.get(p.pid)
            ground_pid = SPICE_DRIED_TO_GROUND.get(dried_pid) if dried_pid else None
            dried_stock = stock_service.get_farm_amount(farm_id, dried_pid) if dried_pid else 0
            ground_f10 = stock_service.get_farm_amount(farm_id, ground_pid) if ground_pid else 0
            ground_f1 = (
                stock_service.farm_stocks[1].get(ground_pid, 0)
                if (has_farm1_stocks and ground_pid)
                else 0
            )
            ground_stock = ground_f10 + ground_f1
            chain_total = raw_stock + dried_stock + ground_stock
            # Reserve protection first (< min_products), then lowest total chain stock
            return (0 if raw_stock < min_products else 1, chain_total, ground_stock, raw_stock)

        candidates.sort(key=_spice_sort_key)
        return candidates

    @staticmethod
    def resolve_candidates(
        strategy_name: str,
        stock_service: StockService,
        category: str = "v",
        min_products: int = 500,
        quest_requirements: dict[int, int] | None = None,
        fixed_pid: int | None = None,
        farm_id: int | None = None,
        quest5_water_candidates: list[tuple[int, int, int, str]] | None = None,
        exclude_pids: set[int] | None = None,
    ) -> list[Product]:
        """Resolve ordered list of plant candidates based on strategy, farm category, and seed availability."""
        excluded = exclude_pids or set()
        has_farm_stocks = bool(
            getattr(stock_service, "farm_stocks", None)
            or getattr(stock_service, "farm_temp_stocks", None)
        )
        is_outer_farm = farm_id in (5, 6, 8, 10)

        if fixed_pid and fixed_pid not in excluded:
            fixed_product = stock_service.get_product(fixed_pid)
            if fixed_product:
                if fixed_product.category != category:
                    farm_label = f"Farm {farm_id}" if farm_id is not None else "diese Farm"
                    logger.warning(
                        f"Feste Vorgabe-PID {fixed_pid} ('{fixed_product.name}') hat Kategorie '{fixed_product.category}', "
                        f"aber {farm_label} erlaubt strikt nur Kategorie '{category}'! "
                        f"Feste Vorgabe wird ignoriert und dynamisch nach '{strategy_name}' ermittelt."
                    )
                else:
                    if (
                        is_outer_farm
                        and has_farm_stocks
                        and stock_service.get_farm_amount(farm_id, fixed_pid) <= 0
                    ):
                        logger.warning(
                            f"Farm {farm_id}: Feste Vorgabe '{fixed_product.name}' (PID {fixed_pid}) "
                            f"hat 0 Saatgut im lokalen Farmregal! Weiche auf dynamische Strategie '{strategy_name}' aus."
                        )
                    else:
                        logger.info(
                            f"Farm-Pflanze (Feste Vorgabe): '{fixed_product.name}' "
                            f"(PID {fixed_product.pid}, Lagerbestand: {fixed_product.total_amount})"
                        )
                        # Fixed product is primary; fallbacks resolved dynamically
                        dynamic_fallbacks = PlantStrategySolver.resolve_candidates(
                            strategy_name=strategy_name,
                            stock_service=stock_service,
                            category=category,
                            min_products=min_products,
                            quest_requirements=quest_requirements,
                            fixed_pid=None,
                            farm_id=farm_id,
                            quest5_water_candidates=quest5_water_candidates,
                            exclude_pids=excluded | {fixed_pid},
                        )
                        return [fixed_product] + dynamic_fallbacks
            else:
                logger.warning(
                    f"Feste Vorgabe-PID {fixed_pid} nicht im Produktkatalog gefunden. "
                    f"Weiche auf Strategie '{strategy_name}' aus."
                )

        strategy = strategy_name.lower()

        # Specific handler for Farm 8 (water farm) and Quest 5 demands
        if (
            (farm_id == 8 or category == "water")
            and "quest" in strategy
            and quest5_water_candidates
        ):
            water_results = []
            for pid, deficit, q_id, reason in quest5_water_candidates:
                if pid in excluded:
                    continue
                seeds = stock_service.get_farm_amount(farm_id, pid)
                prod = stock_service.get_product(pid)
                if not prod or prod.category != category:
                    continue
                if seeds > 0:
                    water_results.append(prod)
                else:
                    logger.debug(
                        f"Farm {farm_id}: Pflanze '{prod.name}' (PID {pid}) wird für {reason} benötigt, "
                        f"hat aber 0 Saatgut im lokalen Regal. Überspringe zum nächsten Quest-5-Bedarf."
                    )
            if water_results:
                best = water_results[0]
                logger.info(
                    f"Strategie '{strategy_name}' (Farm {farm_id} / Quest 5): "
                    f"Gewählte Pflanze '{best.name}' (PID {best.pid})"
                )
                return water_results

        # Specific handler for Farm 10 (spice farm): balance full spice chain (raw + dried + ground)
        if farm_id == 10 or category == "spice":
            eff_farm_id = farm_id if farm_id is not None else 10
            # Check direct raw spice quest requirements first (using local Farm 10 stock)
            if "quest" in strategy and quest_requirements:
                direct_spice_quests: list[Product] = []
                for pid, req_amt in quest_requirements.items():
                    if pid in excluded:
                        continue
                    prod = stock_service.get_product(pid)
                    if (
                        prod
                        and prod.category == "spice"
                        and stock_service.get_farm_amount(eff_farm_id, pid) > 0
                        and stock_service.get_farm_amount(eff_farm_id, pid)
                        < (req_amt + min_products)
                    ):
                        direct_spice_quests.append(prod)
                if direct_spice_quests:
                    direct_spice_quests.sort(
                        key=lambda p: -(
                            (quest_requirements.get(p.pid, 0) + min_products)
                            - stock_service.get_farm_amount(eff_farm_id, p.pid)
                        )
                    )
                    best = direct_spice_quests[0]
                    logger.info(
                        f"Strategie '{strategy_name}' (Farm {eff_farm_id} / Quest-Gewürz): "
                        f"Gewählte Pflanze '{best.name}' (PID {best.pid})"
                    )
                    return direct_spice_quests

            balanced_spices = PlantStrategySolver.get_farm10_balanced_spice_candidates(
                stock_service=stock_service,
                min_products=min_products,
                farm_id=eff_farm_id,
                exclude_pids=excluded,
            )
            if balanced_spices:
                best = balanced_spices[0]
                raw_amt = stock_service.get_farm_amount(eff_farm_id, best.pid)
                dried_pid = SPICE_RAW_TO_DRIED.get(best.pid)
                ground_pid = SPICE_DRIED_TO_GROUND.get(dried_pid) if dried_pid else None
                dried_amt = stock_service.get_farm_amount(eff_farm_id, dried_pid) if dried_pid else 0
                ground_amt = (
                    stock_service.get_farm_amount(eff_farm_id, ground_pid) if ground_pid else 0
                )
                logger.info(
                    f"Strategie '{strategy_name}' (Farm {eff_farm_id} / Balancing): "
                    f"Gewählte Pflanze '{best.name}' (PID {best.pid}, "
                    f"Roh: {raw_amt}, Getrocknet: {dried_amt}, Gemahlen: {ground_amt})"
                )
                return balanced_spices
            logger.info(
                f"Farm {eff_farm_id}: Kein Saatgut für Kategorie 'spice' im lokalen Farmregal vorhanden."
            )
            return []

        if "quest" in strategy and quest_requirements:
            candidates = PlantStrategySolver.get_plant_quest_candidates(
                stock_service,
                quest_requirements=quest_requirements,
                category=category,
                min_products=min_products,
                farm_id=farm_id,
                exclude_pids=excluded,
            )
        else:  # Default to plantMin
            candidates = PlantStrategySolver.get_plant_min_candidates(
                stock_service,
                category=category,
                min_products=min_products,
                farm_id=farm_id,
                exclude_pids=excluded,
            )

        if candidates:
            best = candidates[0]
            logger.info(
                f"Strategie '{strategy_name}' ({category}): Gewählte Pflanze '{best.name}' "
                f"(PID {best.pid}, Bestand: {best.total_amount}/{min_products})"
            )
            return candidates

        # If everything is above min_products, pick the overall lowest stock crop
        all_crops = [
            p
            for p in stock_service.products.values()
            if p.category == category and p.pid not in excluded
        ]
        if all_crops:
            with_seeds = [p for p in all_crops if stock_service.get_farm_amount(farm_id, p.pid) > 0]
            if with_seeds:
                all_crops = with_seeds
            elif is_outer_farm and has_farm_stocks:
                logger.info(
                    f"Farm {farm_id}: Kein Saatgut für Kategorie '{category}' im lokalen Farmregal vorhanden."
                )
                return []

            all_crops.sort(
                key=lambda p: (
                    0 if stock_service.get_farm_amount(farm_id, p.pid) > 0 else 1,
                    p.total_amount,
                )
            )
            return all_crops

        return []

    @staticmethod
    def resolve_candidate(
        strategy_name: str,
        stock_service: StockService,
        category: str = "v",
        min_products: int = 500,
        quest_requirements: dict[int, int] | None = None,
        fixed_pid: int | None = None,
        farm_id: int | None = None,
        quest5_water_candidates: list[tuple[int, int, int, str]] | None = None,
        exclude_pids: set[int] | None = None,
    ) -> Product | None:
        """Resolve the top plant candidate based on the named strategy and farm category."""
        candidates = PlantStrategySolver.resolve_candidates(
            strategy_name=strategy_name,
            stock_service=stock_service,
            category=category,
            min_products=min_products,
            quest_requirements=quest_requirements,
            fixed_pid=fixed_pid,
            farm_id=farm_id,
            quest5_water_candidates=quest5_water_candidates,
            exclude_pids=exclude_pids,
        )
        return candidates[0] if candidates else None
