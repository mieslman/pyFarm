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
        self.state = FlowerAreaState(
            fields={pos: FlowerField(pos=pos, pid=None) for pos in range(1, 37)}
        )

    def update(self, farmersmarket_data: dict) -> None:
        """Parse all 36 flower beds from the farmersmarket data block."""
        fa_raw = farmersmarket_data.get("flower_area")
        if fa_raw is None:
            return

        parsed_fields: dict[int, FlowerField] = {}
        if isinstance(fa_raw, list):
            # Empty list returned by server (e.g. after harvest_all: "flower_area": [])
            parsed_fields = {pos: FlowerField(pos=pos, pid=None) for pos in range(1, 37)}
        elif isinstance(fa_raw, dict):
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
                    field = FlowerField(pos=pos, pid=None)
                parsed_fields[pos] = field
        else:
            return

        self.state.fields = parsed_fields
        logger.debug(
            f"FlowerAreaService: 36 Beete aktualisiert "
            f"({sum(1 for f in self.state.fields.values() if f.is_ready)} erntereif, "
            f"{sum(1 for f in self.state.fields.values() if f.is_empty)} frei)."
        )

    async def harvest(self) -> int:
        """Harvest all flowers on the flower area if ALL planted beds are ready (remain < 0)."""
        planted_fields = [f for f in self.state.fields.values() if not f.is_empty]
        if not planted_fields:
            return 0

        # Check if ALL planted fields are ready (remain < 0)
        if not all(f.is_ready for f in planted_fields):
            logger.debug("Blumenwiese: Nicht alle bepflanzten Beete sind erntereif (remain < 0). Ernte wird zurückgestellt.")
            return 0

        logger.info(f"Blumenwiese: Ernte gesamte Blumenwiese ({len(planted_fields)} Beete)...")
        try:
            res = await self.client.api_call(
                "farm",
                {
                    "mode": "flowerarea_harvest_all",
                    "farm": 1,
                    "position": 1,
                },
            )
            count = len(planted_fields)
            if "updateblock" in res and isinstance(res["updateblock"], dict):
                fm = res["updateblock"].get("farmersmarket", {})
                if "flower_area" in fm:
                    self.update(fm)
                else:
                    for f in self.state.fields.values():
                        f.pid = None
                        f.remain = 0
                        f.water_remain = 0
                if self.stock_service:
                    self.stock_service.update(res)
            else:
                for f in self.state.fields.values():
                    f.pid = None
                    f.remain = 0
                    f.water_remain = 0

            return count
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Blumenwiese: Fehler beim Gesamternten: {e}")
            return 0

    async def water(self) -> bool:
        """Water all active flower beds if any planted bed needs water (water_remain < 0)."""
        needs_water = any(f.needs_water for f in self.state.fields.values())
        if not needs_water:
            return False

        logger.info("Blumenwiese: Bewässere alle Blumenbeete (flowerarea_water_all)...")
        try:
            res = await self.client.api_call(
                "farm",
                {
                    "mode": "flowerarea_water_all",
                    "farm": 1,
                    "position": 1,
                },
            )
            if "updateblock" in res and isinstance(res["updateblock"], dict):
                fm = res["updateblock"].get("farmersmarket", {})
                if "flower_area" in fm:
                    self.update(fm)
                else:
                    for f in self.state.fields.values():
                        if not f.is_empty:
                            f.water_remain = 86400
                if self.stock_service:
                    self.stock_service.update(res)
            else:
                for f in self.state.fields.values():
                    if not f.is_empty:
                        f.water_remain = 86400

            return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Blumenwiese: Fehler beim Bewässern: {e}")
            return False

    async def plant(self) -> int:
        """Plant flower seeds on the flower area using flowerarea_autoplant.

        Selects the flower seed PID (category 'fl') with the smallest available quantity in stock.
        """
        empty_fields = [f for f in self.state.fields.values() if f.is_empty]
        if not empty_fields:
            return 0

        best_pid = self._get_best_flower_seed()
        if best_pid is None:
            logger.debug("Blumenwiese: Keine Blumensamen (Kategorie 'fl') im Lager vorrätig.")
            return 0

        logger.info(f"Blumenwiese: Bepflanze Blumenwiese mit PID {best_pid} (geringster Bestand via flowerarea_autoplant)...")
        try:
            res = await self.client.api_call(
                "farm",
                {
                    "mode": "flowerarea_autoplant",
                    "farm": 1,
                    "position": 1,
                    "set": 0,
                    "pid": best_pid,
                },
            )
            if "updateblock" in res and isinstance(res["updateblock"], dict):
                fm = res["updateblock"].get("farmersmarket", {})
                if "flower_area" in fm:
                    self.update(fm)
                else:
                    for f in self.state.fields.values():
                        f.pid = best_pid
                        f.remain = 18000
                if self.stock_service:
                    self.stock_service.update(res)
            else:
                for f in self.state.fields.values():
                    f.pid = best_pid
                    f.remain = 18000

            return len([f for f in self.state.fields.values() if f.pid == best_pid])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Blumenwiese: Fehler beim Bepflanzen: {e}")
            return 0

    def _get_best_flower_seed(self) -> int | None:
        """Find flower seed PID (category 'fl') with smallest stock quantity.

        Only considers plantable seeds with price > 0 (avoiding special items or Tigerlilie).
        """
        if not self.stock_service or not self.stock_service.products:
            return None

        candidates: list[tuple[int, int]] = []
        for pid, product in self.stock_service.products.items():
            if (
                getattr(product, "category", "") == "fl"
                and product.amount > 0
                and getattr(product, "price", 0) > 0
            ):
                candidates.append((pid, product.amount))

        if not candidates:
            return None

        # Sort by quantity ascending; tie-break by PID
        candidates.sort(key=lambda item: (item[1], item[0]))
        return candidates[0][0]
