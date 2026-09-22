from typing import Any

import httpx
from loguru import logger

from app.config import StockConfig, settings
from app.core.client import MFFGameClient
from app.core.exceptions import MFFException
from app.models.product import Product
from app.services.catalog import load_remote_catalog
from app.services.market_service import MarketService
from app.services.seed_dealer_service import SeedDealerService


class StockService:
    """Central inventory manager and raw materials grasping coordinator."""

    def __init__(
        self,
        client: MFFGameClient,
        config: StockConfig | None = None,
    ):
        self.client = client
        self.config = config or settings.stock
        self.products: dict[int, Product] = {}
        self.farm_stocks: dict[int, dict[int, int]] = {}
        self.farm_temp_stocks: dict[int, dict[int, int]] = {}
        self.market = MarketService(client)
        self.seed_dealer = SeedDealerService(client)
        self.credits_kt: float = 0.0

    async def init(self, login_html: str, initial_data: dict[str, Any] | None = None):
        """Initialize the product catalog from login HTML/JS and set initial stock levels."""
        logger.info("---------- StockService: Initialisierung ----------")
        self.products = await load_remote_catalog(self.client, login_html)

        if initial_data:
            self.update(initial_data)
        else:
            logger.info("Rufe initialen Farm- und Lagerbestand ab (getfarms)...")
            res = await self.client.api_call("farm", {"mode": "getfarms", "farm": 1, "position": 0})
            self.update(res)

    def init_with_catalog(
        self, catalog: dict[int, Product], initial_data: dict[str, Any] | None = None
    ):
        """Synchronous catalog initialization (e.g. for offline tests)."""
        self.products = catalog
        if initial_data:
            self.update(initial_data)

    def update(self, body: dict[str, Any] | None = None):
        """Update in-memory product stock from game response updateblock.stock."""
        if not body or not isinstance(body, dict):
            return

        updateblock = body.get("updateblock")
        if not isinstance(updateblock, dict):
            return

        # Update available kT balance if present
        menue = updateblock.get("menue")
        if isinstance(menue, dict) and "bar" in menue:
            try:
                self.credits_kt = float(menue["bar"])
                logger.debug(f"Aktueller Kontostand: {self.credits_kt:.2f} kT")
            except (ValueError, TypeError):
                pass

        stock_data = updateblock.get("stock")
        if not isinstance(stock_data, dict):
            return

        # Reset current amounts
        for product in self.products.values():
            product.amount = 0
            product.tmp_amount = 0

        self.farm_stocks: dict[int, dict[int, int]] = {}

        # Parse inventory racks for all farms
        raw_racks = stock_data.get("stock", {})
        if isinstance(raw_racks, dict):
            for farm_str, farm_shelf in raw_racks.items():
                if str(farm_str).isdigit() and isinstance(farm_shelf, dict):
                    f_id = int(farm_str)
                    self.farm_stocks[f_id] = {}
                    for rack in farm_shelf.values():
                        if isinstance(rack, dict):
                            for item in rack.values():
                                if isinstance(item, dict) and "pid" in item and "amount" in item:
                                    try:
                                        pid = int(item["pid"])
                                        amt = int(item["amount"])
                                        self.farm_stocks[f_id][pid] = (
                                            self.farm_stocks[f_id].get(pid, 0) + amt
                                        )
                                    except (ValueError, TypeError):
                                        continue

        # Sync product amounts: Farm 1 represents main stock
        if 1 in self.farm_stocks:
            for pid, prod in self.products.items():
                prod.amount = self.farm_stocks[1].get(pid, 0)

        # Parse temporary / fast-access stock (tempstock)
        self.farm_temp_stocks = {}
        raw_tempstock = stock_data.get("tempstock", {})
        if isinstance(raw_tempstock, dict):
            for pid_str, slot_data in raw_tempstock.items():
                if str(pid_str).isdigit():
                    pid = int(pid_str)
                    if isinstance(slot_data, dict):
                        for f_key, amt_val in slot_data.items():
                            if str(f_key).isdigit():
                                f_id = int(f_key)
                                try:
                                    amt = int(amt_val)
                                    if f_id not in self.farm_temp_stocks:
                                        self.farm_temp_stocks[f_id] = {}
                                    self.farm_temp_stocks[f_id][pid] = amt
                                except (ValueError, TypeError):
                                    pass
                    elif isinstance(slot_data, (int, str)):
                        try:
                            amt = int(slot_data)
                            if 1 not in self.farm_temp_stocks:
                                self.farm_temp_stocks[1] = {}
                            self.farm_temp_stocks[1][pid] = amt
                        except ValueError:
                            pass

                    if pid in self.products:
                        tmp_qty = 0
                        if isinstance(slot_data, dict):
                            # Slot indices in tempstock: 5, 6, 8, 10
                            for slot_key in ("5", "6", "8", "10", 5, 6, 8, 10):
                                if slot_key in slot_data:
                                    try:
                                        tmp_qty = int(slot_data[slot_key])
                                        break
                                    except (ValueError, TypeError):
                                        pass
                            if tmp_qty == 0 and slot_data:
                                try:
                                    tmp_qty = int(next(iter(slot_data.values())))
                                except (ValueError, TypeError):
                                    pass
                        elif isinstance(slot_data, (int, str)):
                            try:
                                tmp_qty = int(slot_data)
                            except ValueError:
                                pass
                        self.products[pid].tmp_amount = tmp_qty

        total_items = sum(p.amount for p in self.products.values())
        logger.debug(f"Lager aktualisiert: {total_items} Einheiten im Hauptbestand.")

    def get_product(self, pid: int) -> Product | None:
        """Get product model by product ID."""
        return self.products.get(pid)

    def get_product_by_name(self, name: str) -> Product | None:
        """Find product model by exact display name."""
        for product in self.products.values():
            if product.name.lower() == name.lower():
                return product
        return None

    def get_available_amount(self, pid: int) -> int:
        """Get total available amount (main inventory + tempstock)."""
        product = self.get_product(pid)
        return product.total_amount if product else 0

    def get_stock(self, pid: int) -> int:
        """Get stock of a product in main inventory (Farm 1)."""
        return self.get_farm_amount(farm_id=1, pid=pid, include_fallback=True)

    def get_farm_amount(
        self, farm_id: int | None, pid: int, include_fallback: bool = False
    ) -> int:
        """Get available stock of a product on a specific farm rack.

        If include_fallback is True and the product is not found on this farm rack,
        falls back to main inventory (farm 1).
        """
        if farm_id is None or farm_id in (1, 2, 3, 4):
            if hasattr(self, "farm_stocks") and 1 in self.farm_stocks:
                return self.farm_stocks[1].get(pid, 0)
            return self.get_available_amount(pid)

        amt = 0
        if hasattr(self, "farm_stocks") and farm_id in self.farm_stocks:
            amt = self.farm_stocks[farm_id].get(pid, 0)
        if amt == 0 and hasattr(self, "farm_temp_stocks") and farm_id in self.farm_temp_stocks:
            amt = self.farm_temp_stocks[farm_id].get(pid, 0)

        if amt > 0:
            return amt

        if include_fallback:
            main_amt = self.farm_stocks.get(1, {}).get(pid, 0) if hasattr(self, "farm_stocks") else 0
            if main_amt > 0:
                return main_amt

        # Fallback if farm_stocks and farm_temp_stocks were not populated at all (e.g. in offline unit tests)
        if not (hasattr(self, "farm_stocks") and self.farm_stocks) and not (
            hasattr(self, "farm_temp_stocks") and self.farm_temp_stocks
        ):
            product = self.get_product(pid)
            return product.amount if product else 0

        return 0

    def get_total_stock(self, pid: int) -> int:
        """Get total stock of a product across all farm racks and temporary stock."""
        if hasattr(self, "farm_stocks") and self.farm_stocks:
            total = sum(f_stock.get(pid, 0) for f_stock in self.farm_stocks.values())
            prod = self.get_product(pid)
            if prod:
                total += prod.tmp_amount
            return total
        product = self.get_product(pid)
        return product.total_amount if product else 0

    def deduct_stock(self, pid: int, amount: int, farm_id: int | None = None):
        """Locally deduct amount from stock (e.g. when initiating production)."""
        if farm_id is not None:
            if (
                hasattr(self, "farm_stocks")
                and farm_id in self.farm_stocks
                and pid in self.farm_stocks[farm_id]
            ):
                self.farm_stocks[farm_id][pid] = max(0, self.farm_stocks[farm_id][pid] - amount)
            if (
                hasattr(self, "farm_temp_stocks")
                and farm_id in self.farm_temp_stocks
                and pid in self.farm_temp_stocks[farm_id]
            ):
                self.farm_temp_stocks[farm_id][pid] = max(0, self.farm_temp_stocks[farm_id][pid] - amount)
        if pid in self.products:
            self.products[pid].amount = max(0, self.products[pid].amount - amount)

    def get_temp_amount(self, farm_id: int | None, pid: int) -> int:
        """Get temporary / shelf stock for a specific farm and product."""
        if farm_id is not None:
            if hasattr(self, "farm_temp_stocks") and self.farm_temp_stocks and farm_id in self.farm_temp_stocks:
                amt = self.farm_temp_stocks[farm_id].get(pid, 0)
                if amt > 0:
                    return amt
            if hasattr(self, "farm_stocks") and self.farm_stocks and farm_id in self.farm_stocks:
                amt = self.farm_stocks[farm_id].get(pid, 0)
                if amt > 0:
                    return amt
            return 0
        product = self.get_product(pid)
        return product.tmp_amount if product else 0

    def deduct_temp_stock(self, pid: int, amount: int, farm_id: int | None = None):
        """Locally deduct amount from temporary / outer farm rack stock."""
        if farm_id is not None:
            if (
                hasattr(self, "farm_temp_stocks")
                and farm_id in self.farm_temp_stocks
                and pid in self.farm_temp_stocks[farm_id]
            ):
                self.farm_temp_stocks[farm_id][pid] = max(
                    0, self.farm_temp_stocks[farm_id][pid] - amount
                )
            if (
                hasattr(self, "farm_stocks")
                and farm_id in self.farm_stocks
                and pid in self.farm_stocks[farm_id]
            ):
                self.farm_stocks[farm_id][pid] = max(
                    0, self.farm_stocks[farm_id][pid] - amount
                )
        if pid in self.products:
            self.products[pid].tmp_amount = max(0, self.products[pid].tmp_amount - amount)

    def get_amount(self, pid: int) -> int:
        """Get main inventory amount for a product ID."""
        product = self.get_product(pid)
        return product.amount if product else 0

    async def grasp_products(self, requirements: list[dict[str, int]]) -> bool:
        """Ensure required products are in stock; buy missing quantities via market or seed dealer.

        Parameters:
        - requirements: list of dicts with 'pid' and 'amount', e.g. [{'pid': 17, 'amount': 120}]

        Returns:
        - True if all products are available in required quantities, False otherwise.
        """
        all_available = True

        for req in requirements:
            pid = int(req["pid"])
            needed_amount = int(req["amount"])
            product = self.get_product(pid)

            if not product:
                logger.warning(f"Grasping: Unbekanntes Produkt mit PID {pid}.")
                all_available = False
                continue

            suppl = (
                self.config.buffer_crops if product.category == "v" else self.config.buffer_other
            )
            price_factor = (
                self.config.max_price_factor_crops
                if product.category == "v"
                else self.config.max_price_factor_other
            )

            # Check if current stock minus reserve buffer satisfies the need
            if product.amount - suppl < needed_amount:
                to_buy = needed_amount - product.amount + suppl
                logger.info(
                    f"Grasping: Fehlmenge für '{product.name}' (PID {pid}): "
                    f"Bestand {product.amount} < Benötigt {needed_amount} + Puffer {suppl}. "
                    f"Muss {to_buy} Stück nachkaufen..."
                )

                # Check minimum credit protection
                if self.credits_kt < self.config.min_credit_kt:
                    logger.warning(
                        f"Grasping abgebrochen: Guthaben ({self.credits_kt:.2f} kT) "
                        f"unter Minimal-Limit ({self.config.min_credit_kt:.2f} kT)."
                    )
                    if product.amount >= needed_amount:
                        logger.info(
                            f"Grasping: Vorhandener Bestand ({product.amount}) deckt Rohbedarf ({needed_amount}), fahre fort."
                        )
                        continue
                    return False

                # 1. Search and buy from player marketplace
                max_market_price = product.price * price_factor
                bought_market = await self.market.buy(
                    pid=pid, amount=to_buy, max_price=max_market_price
                )
                to_buy -= bought_market

                # 2. If crop (category 'v') and quantity still missing: fallback to NPC seed dealer
                if to_buy > 0 and product.category == "v":
                    bought_dealer = await self.seed_dealer.buy(pid=pid, amount=to_buy)
                    to_buy -= bought_dealer

                # Refresh stock after purchasing
                if bought_market > 0 or (product.category == "v" and to_buy < needed_amount):
                    # Trigger farm status to fetch updated stock
                    try:
                        res = await self.client.api_call(
                            "farm", {"mode": "getfarms", "farm": 1, "position": 0}
                        )
                        self.update(res)
                    except (MFFException, httpx.HTTPError) as e:
                        logger.warning(f"Konnte Lager nach Zukauf nicht aktualisieren: {e}")

                if to_buy > 0:
                    logger.warning(
                        f"Grasping fehlgeschlagen: Konnte {to_buy}x '{product.name}' nicht beschaffen."
                    )
                    all_available = False
                else:
                    logger.info(f"Grasping erfolgreich: '{product.name}' vollständig beschafft.")

        return all_available
