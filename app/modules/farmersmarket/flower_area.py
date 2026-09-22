"""Blumenwiese (FlowerArea) service for planting, watering, and harvesting flower beds."""

from loguru import logger

from app.core.client import MFFGameClient
from app.modules.farmersmarket.models import FlowerAreaState, FlowerField
from app.services.stock_service import StockService


class FlowerAreaService:
    """Automates harvesting, watering, and planting of the 36 flower beds in Dorf 2."""

    def __init__(self, client: MFFGameClient, stock_service: StockService | None = None) -> None:
        self.client = client
        self.stock_service = stock_service
        self.state = FlowerAreaState()

    def update(self, farmersmarket_data: dict) -> None:
        """Parse all 36 flower beds from the farmersmarket data block."""
        fa_raw = farmersmarket_data.get("flower_area", {})
        if not isinstance(fa_raw, dict):
            return

        parsed_fields: dict[int, FlowerField] = {}
        for pos in range(1, 37):
            pos_str = str(pos)
            f_data = fa_raw.get(pos_str)
            if f_data and isinstance(f_data, dict):
                pid_val = f_data.get("pid")
                pid = int(pid_val) if pid_val is not None and str(pid_val).isdigit() else None
                field = FlowerField(
                    pos=pos,
                    pid=pid,
                    remain=int(f_data.get("remain", 0) or 0),
                    water_remain=int(f_data.get("water_remain", 0) or 0),
                    duration=int(f_data.get("duration", 0) or 0),
                    createdate=int(f_data.get("createdate", 0) or 0),
                )
            else:
                # Field is currently unplanted / empty
                field = FlowerField(pos=pos, pid=None)

            parsed_fields[pos] = field

        self.state.fields = parsed_fields
        logger.debug(
            f"FlowerAreaService: 36 Beete aktualisiert "
            f"({sum(1 for f in self.state.fields.values() if f.is_ready)} erntereif, "
            f"{sum(1 for f in self.state.fields.values() if f.is_empty)} frei)."
        )

    async def harvest(self) -> int:
        """Harvest all ready flowers on the 36 flower beds."""
        ready_fields = [f for f in self.state.fields.values() if f.is_ready]
        if not ready_fields:
            return 0

        harvested_count = 0
        logger.info(f"Blumenwiese: Ernte {len(ready_fields)} reife Blumenbeete...")
        for field in ready_fields:
            try:
                res = await self.client.api_call(
                    "farm",
                    {
                        "mode": "flowerarea_harvest",
                        "farm": 1,
                        "position": 1,
                        "set": f"{field.pos}:{field.pid}",
                    },
                )
                harvested_count += 1
                field.pid = None
                field.remain = 0
                field.water_remain = 0
                if "updateblock" in res and self.stock_service:
                    self.stock_service.update(res)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Blumenwiese: Fehler beim Ernten von Beet #{field.pos}: {e}")

        return harvested_count

    async def water(self) -> bool:
        """Water all active flower beds if any of them need water."""
        needs_water = any(f.needs_water for f in self.state.fields.values())
        if not needs_water:
            return False

        logger.info("Blumenwiese: Bewässere alle Blumenbeete...")
        try:
            res = await self.client.api_call(
                "farm",
                {
                    "mode": "flowerarea_water_all",
                    "farm": 1,
                    "position": 1,
                },
            )
            for f in self.state.fields.values():
                if not f.is_empty:
                    f.water_remain = 86400  # 24h reset
            if "updateblock" in res and self.stock_service:
                self.stock_service.update(res)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Blumenwiese: Fehler beim Bewässern: {e}")
            return False

    async def plant(self, max_batch: int = 6) -> int:
        """Plant available flower seeds on empty flower beds."""
        empty_fields = [f for f in self.state.fields.values() if f.is_empty]
        if not empty_fields:
            return 0

        # Retrieve flower seeds from stock
        available_flowers = self._get_available_flower_seeds()
        if not available_flowers:
            logger.debug("Blumenwiese: Keine Blumensamen im Lager vorrätig.")
            return 0

        planted_count = 0
        empty_idx = 0

        for flower_pid, flower_amt in available_flowers:
            if empty_idx >= len(empty_fields):
                break

            to_plant = min(flower_amt, max_batch, len(empty_fields) - empty_idx)
            if to_plant <= 0:
                continue

            logger.info(f"Blumenwiese: Pflanze {to_plant}x Blume PID {flower_pid}...")
            for _ in range(to_plant):
                if empty_idx >= len(empty_fields):
                    break
                field = empty_fields[empty_idx]
                try:
                    res = await self.client.api_call(
                        "farm",
                        {
                            "mode": "flowerarea_plant",
                            "farm": 1,
                            "position": 1,
                            "set": f"{field.pos}:{flower_pid},",
                        },
                    )
                    planted_count += 1
                    field.pid = flower_pid
                    field.remain = 18000  # Default estimate
                    if "updateblock" in res and self.stock_service:
                        self.stock_service.update(res)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Blumenwiese: Fehler beim Bepflanzen von Beet #{field.pos}: {e}")
                finally:
                    empty_idx += 1

        return planted_count

    def _get_available_flower_seeds(self) -> list[tuple[int, int]]:
        """Get list of flower seed PIDs and available quantities (category 'fl').

        Only considers plantable seeds with price > 0 (avoiding special items or Tigerlilie)
        and prioritizes varieties not yet growing on the 36 beds to ensure biodiversity.
        """
        if not self.stock_service:
            return []

        # Find all plantable flowers (category 'fl', amount > 0, price > 0)
        flower_candidates: list[tuple[int, int]] = []
        for pid, product in self.stock_service.products.items():
            if (
                getattr(product, "category", "") == "fl"
                and product.amount > 0
                and getattr(product, "price", 0) > 0
            ):
                flower_candidates.append((pid, product.amount))

        # Check which flowers are already planted on the 36 beds
        currently_planted_pids = {f.pid for f in self.state.fields.values() if not f.is_empty and f.pid}

        # Partition into unplanted vs already planted
        unplanted = [c for c in flower_candidates if c[0] not in currently_planted_pids]
        already_planted = [c for c in flower_candidates if c[0] in currently_planted_pids]

        unplanted.sort(key=lambda item: item[1])
        already_planted.sort(key=lambda item: item[1])

        # Prioritize varieties not currently planted first, then fall back to already planted
        return unplanted + already_planted

