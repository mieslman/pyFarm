from loguru import logger

from app.models.product import Product
from app.services.stock_service import StockService


class PlantStrategySolver:
    """Calculates the best crop candidate to plant based on inventory and objectives."""

    @staticmethod
    def get_plant_min_candidates(
        stock_service: StockService,
        category: str = "v",
        min_products: int = 500,
        farm_id: int | None = None,
    ) -> list[Product]:
        """Rank crops by lowest available inventory to maintain balanced stock levels.

        Prioritizes crops where seeds are currently available in the farm's local rack.
        """
        candidates = [
            p
            for p in stock_service.products.values()
            if p.category == category and p.total_amount < min_products
        ]
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
    ) -> list[Product]:
        """Prioritize crops required to complete active quests.

        Prioritizes crops with available seeds in the farm's local rack.
        """
        quest_candidates = []
        for pid, required_amount in quest_requirements.items():
            product = stock_service.get_product(pid)
            if (
                product
                and product.category == category
                and product.total_amount < (required_amount + min_products)
            ):
                quest_candidates.append(product)

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
            )

        return quest_candidates

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
    ) -> Product | None:
        """Resolve the top plant candidate based on the named strategy and farm category."""
        if fixed_pid:
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
                    logger.info(
                        f"Farm-Pflanze (Feste Vorgabe): '{fixed_product.name}' "
                        f"(PID {fixed_product.pid}, Lagerbestand: {fixed_product.total_amount})"
                    )
                    return fixed_product
            else:
                logger.warning(
                    f"Feste Vorgabe-PID {fixed_pid} nicht im Produktkatalog gefunden. "
                    f"Weiche auf Strategie '{strategy_name}' aus."
                )

        strategy = strategy_name.lower()

        # Specific handler for Farm 8 (water farm) and Quest 5 demands
        if (farm_id == 8 or category == "water") and "quest" in strategy and quest5_water_candidates:
            for pid, deficit, q_id, reason in quest5_water_candidates:
                seeds = stock_service.get_farm_amount(farm_id, pid)
                prod = stock_service.get_product(pid)
                if not prod or prod.category != category:
                    continue
                if seeds > 0:
                    logger.info(
                        f"Strategie '{strategy_name}' (Farm {farm_id} / Quest 5): "
                        f"Gewählte Pflanze '{prod.name}' (PID {pid}, Fehlmenge: {deficit}, "
                        f"Grund: {reason}, Saatgut auf Farm: {seeds})"
                    )
                    return prod
                logger.debug(
                    f"Farm {farm_id}: Pflanze '{prod.name}' (PID {pid}) wird für {reason} benötigt, "
                    f"hat aber 0 Saatgut im lokalen Regal. Überspringe zum nächsten Quest-5-Bedarf."
                )

        if "quest" in strategy and quest_requirements:
            candidates = PlantStrategySolver.get_plant_quest_candidates(
                stock_service,
                quest_requirements=quest_requirements,
                category=category,
                min_products=min_products,
                farm_id=farm_id,
            )
        else:  # Default to plantMin
            candidates = PlantStrategySolver.get_plant_min_candidates(
                stock_service,
                category=category,
                min_products=min_products,
                farm_id=farm_id,
            )

        if candidates:
            best = candidates[0]
            logger.info(
                f"Strategie '{strategy_name}' ({category}): Gewählte Pflanze '{best.name}' "
                f"(PID {best.pid}, Bestand: {best.total_amount}/{min_products})"
            )
            return best

        # If everything is above min_products, pick the overall lowest stock crop
        all_crops = [p for p in stock_service.products.values() if p.category == category]
        if all_crops:
            all_crops.sort(
                key=lambda p: (
                    0 if stock_service.get_farm_amount(farm_id, p.pid) > 0 else 1,
                    p.total_amount,
                )
            )
            return all_crops[0]

        return None
