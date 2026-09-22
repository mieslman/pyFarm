import math
from typing import Any

from loguru import logger

from app.core.client import MFFGameClient
from app.models.farm import BarnData
from app.services.stock_service import StockService


class Shed:
    """Represents an animal barn (e.g. chicken coop, cow shed) with feeding cost optimization."""

    def __init__(
        self,
        client: MFFGameClient,
        farm_id: int,
        position: int,
        building_id: int = 2,
        name: str = "Stall",
    ):
        self.client = client
        self.farm_id = farm_id
        self.position = position
        self.building_id = building_id
        self.name = name
        self.barn: BarnData | None = None

    async def update(self, body: dict[str, Any] | None = None) -> BarnData | None:
        """Fetch current barn status via inner_init and update BarnData model."""
        if not body:
            body = await self.client.api_call(
                "farm",
                {"mode": "inner_init", "farm": self.farm_id, "position": self.position},
            )

        datablock = body.get("datablock")
        if not datablock or datablock[0] != 1 or len(datablock) < 2:
            if (
                datablock
                and len(datablock) > 1
                and isinstance(datablock[1], str)
                and "keine tiere" in datablock[1].lower()
            ):
                logger.debug(
                    f"Stall {self.farm_id}/{self.position} ist derzeit unbesetzt (keine Tiere)."
                )
                self.barn = None
                return self.barn
            logger.warning(
                f"inner_init für Stall {self.farm_id}/{self.position} lieferte ungültigen datablock."
            )
            return self.barn

        # datablock[1] format: {farm_id: {position: {...}}}
        farm_dict = datablock[1]
        farm_data = farm_dict.get(str(self.farm_id)) or farm_dict.get(self.farm_id, {})
        raw_barn = farm_data.get(str(self.position)) or farm_data.get(self.position, {})

        if not raw_barn or not isinstance(raw_barn, dict):
            logger.warning(
                f"Keine Stalldaten für Position {self.farm_id}/{self.position} gefunden."
            )
            return self.barn

        # Parse animal count
        animals = raw_barn.get("animals", {})
        animals_count = (
            int(animals.get("amount", 0)) if isinstance(animals, dict) else int(animals or 0)
        )

        # Parse feeding options
        raw_feed = raw_barn.get("feed", {})
        feed_options = {}
        if isinstance(raw_feed, dict):
            for pid_str, f_data in raw_feed.items():
                if str(pid_str).isdigit() and isinstance(f_data, dict):
                    feed_options[int(pid_str)] = int(f_data.get("time", 3600))

        self.barn = BarnData(
            farm_id=self.farm_id,
            position=self.position,
            building_id=self.building_id,
            product_id=int(raw_barn.get("pid", 0)),
            animals_count=animals_count,
            remain_seconds=int(raw_barn.get("remain", 0)),
            rest_seconds=int(raw_barn.get("rest", 0)),
            feed_options=feed_options,
        )

        return self.barn

    async def crop(self) -> bool:
        """Pick up ready animal products if production cycle has finished."""
        if not self.barn:
            await self.update()

        if self.barn and self.barn.remain_seconds <= 1:
            logger.info(
                f"Stall {self.farm_id}/{self.position} ({self.name}): "
                f"Ernte tierische Erzeugnisse (PID {self.barn.product_id})..."
            )
            body = await self.client.api_call(
                "farm",
                {"mode": "inner_crop", "farm": self.farm_id, "position": self.position},
            )
            await self.update(body)
            return True

        return False

    async def feed(self, stock_service: StockService) -> bool:
        """Calculate cheapest feed and feed animals if barn is idling."""
        if not self.barn:
            await self.update()

        if not self.barn or self.barn.remain_seconds > 1:
            logger.debug(
                f"Stall {self.farm_id}/{self.position}: Produktion läuft noch ({self.barn.remain_seconds if self.barn else '?'}s)."
            )
            return False

        if not self.barn.feed_options or self.barn.animals_count <= 0:
            logger.warning(
                f"Stall {self.farm_id}/{self.position}: Keine Futteroptionen oder keine Tiere vorhanden."
            )
            return False

        # --- Mathematische Futterkosten-Minimierung ---
        # Berechne für jedes Futter die Gesamtkosten K_i = Einheiten_pro_Tier * Tiere * Preis
        cheapest_pid: int | None = None
        min_total_cost = float("inf")
        needed_amount_for_cheapest = 0

        rest_time = self.barn.rest_seconds if self.barn.rest_seconds > 0 else 7200

        for pid, time_reduction in self.barn.feed_options.items():
            if time_reduction <= 0:
                continue

            units_per_animal = max(1, math.floor(rest_time / time_reduction))
            total_needed = units_per_animal * self.barn.animals_count

            product = stock_service.get_product(pid)
            price_per_unit = product.price if product and product.price > 0 else 0.50
            total_cost = total_needed * price_per_unit

            if total_cost < min_total_cost:
                min_total_cost = total_cost
                cheapest_pid = pid
                needed_amount_for_cheapest = total_needed

        if not cheapest_pid:
            logger.warning(f"Stall {self.farm_id}/{self.position}: Konnte kein Futter berechnen.")
            return False

        feed_product = stock_service.get_product(cheapest_pid)
        feed_name = feed_product.name if feed_product else f"PID {cheapest_pid}"

        logger.info(
            f"Stall {self.farm_id}/{self.position}: Günstigstes Futter ist '{feed_name}' "
            f"(Bedarf: {needed_amount_for_cheapest} Stk, Gesamtkosten: {min_total_cost:.2f} kT)..."
        )

        # Ensure feed is in stock (grasp from market / dealer if needed)
        available = await stock_service.grasp_products(
            [{"pid": cheapest_pid, "amount": needed_amount_for_cheapest}]
        )

        if available:
            logger.info(
                f"Stall {self.farm_id}/{self.position}: Füttere {needed_amount_for_cheapest}x '{feed_name}'..."
            )
            body = await self.client.api_call(
                "farm",
                {
                    "mode": "inner_feed",
                    "farm": self.farm_id,
                    "position": self.position,
                    "pid": cheapest_pid,
                    "amount": needed_amount_for_cheapest,
                },
            )
            await self.update(body)
            return True

        logger.warning(
            f"Stall {self.farm_id}/{self.position}: Fütterung fehlgeschlagen, Futter nicht verfügbar."
        )
        return False

    async def serve(self, stock_service: StockService) -> bool:
        """Serve animal shed: update -> crop -> feed."""
        logger.info(f"--- Stall {self.farm_id}/{self.position} ({self.name}) wird bedient ---")
        await self.update()
        cropped = await self.crop()
        fed = await self.feed(stock_service)
        return cropped or fed
