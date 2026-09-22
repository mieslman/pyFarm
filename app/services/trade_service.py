from typing import Any

import httpx
from loguru import logger

from app.config import TradeConfig, TradeRule, settings
from app.core.client import MFFGameClient
from app.core.exceptions import MFFException, UpstreamAPIError
from app.services.stock_service import StockService


class TradeService:
    """Automates selling surplus inventory on the player marketplace."""

    def __init__(
        self,
        client: MFFGameClient,
        stock_service: StockService,
        config: TradeConfig | None = None,
    ):
        self.client = client
        self.stock_service = stock_service
        self.config = config or settings.trade
        self.market_full: bool = False

    async def determine_sell_price(self, pid: int, target_price: float | None = None) -> float:
        """Determine competitive market price by underbidding lowest offer or using base catalog price."""
        if target_price is not None and target_price > 0:
            return round(target_price, 2)

        product = self.stock_service.get_product(pid)
        base_price = product.price if product else 1.0

        try:
            offers = await self.stock_service.market.get_offers(pid)
        except (UpstreamAPIError, MFFException, httpx.HTTPError) as e:
            logger.warning(f"TradeService: Konnte Marktangebote für PID {pid} nicht abrufen: {e}")
            if "failed" in str(e).lower():
                self.market_full = True
            offers = []

        if offers:
            lowest_offer = offers[0].price
            # Underbid lowest offer slightly, but never below base catalog price
            price = max(base_price, lowest_offer - self.config.underbid_offset_kt)
        else:
            # No market offers active: list at a slight premium over base price (e.g. 20% markup)
            price = round(base_price * 1.2, 2)

        return round(price, 2)

    async def create_offer(self, pid: int, amount: int, price: float) -> bool:
        """Place a sale offer on the player market."""
        if amount <= 0:
            return False

        product = self.stock_service.get_product(pid)
        if product and (
            (product.category == "v" and not self.config.sell_category_v)
            or (self.config.exclude_categories and product.category in self.config.exclude_categories)
        ):
            logger.warning(
                f"TradeService: Erstellen von Marktangebot für {amount}x PID {pid} ('{product.name}') abgebrochen: "
                f"Kategorie '{product.category}' (normale Produkte) ist vom Marktverkauf ausgeschlossen."
            )
            return False

        logger.info(
            f"TradeService: Erstelle Marktangebot für {amount}x PID {pid} zu je {price:.2f} kT..."
        )
        try:
            res = await self.client.api_call(
                "city",
                {
                    "mode": "marketcreateoffer",
                    "pid": pid,
                    "amount": amount,
                    "price": price,
                    "comp": 1,
                },
            )
            datablock = res.get("datablock", [None])
            success = (
                datablock[0] == 1
                if isinstance(datablock, (list, tuple)) and datablock
                else datablock == 1
            )
            if success:
                self.stock_service.update(res)
                logger.info(
                    f"TradeService: Angebot für {amount}x PID {pid} erfolgreich eingestellt."
                )
                return True
            else:
                logger.warning(f"TradeService: Marktangebot abgewiesen: {res}")
                block_msg = str(res).lower()
                if "20 angebote" in block_msg or "nicht möglich" in block_msg or "voll" in block_msg:
                    logger.warning(
                        "TradeService: Maximale Anzahl an Marktangeboten erreicht (20 Angebote). "
                        "Breche weitere Marktverkäufe in diesem Zyklus ab."
                    )
                    self.market_full = True
                return False
        except UpstreamAPIError as e:
            logger.error(
                f"TradeService: Fehler beim Erstellen des Marktangebots für PID {pid}: {e}"
            )
            self.market_full = True
            return False

    async def sell_surplus(self) -> int:
        """Evaluate inventory and list eligible surplus products on the market."""
        if not self.config.enabled:
            return 0

        # Check credit protection
        if self.stock_service.credits_kt < self.config.min_credit_kt:
            logger.warning(
                f"TradeService: Guthaben ({self.stock_service.credits_kt:.2f} kT) "
                f"unter Mindestgrenze ({self.config.min_credit_kt:.2f} kT). Überspringe Marktverkäufe."
            )
            return 0

        self.market_full = False
        offers_created = 0

        # Build map of explicit rules by pid
        rule_map: dict[int, TradeRule] = {r.pid: r for r in self.config.sell_rules}

        # Determine which products to examine
        candidate_pids = set(rule_map.keys())
        if self.config.auto_sell_surplus:
            candidate_pids.update(self.stock_service.products.keys())

        for pid in sorted(candidate_pids):
            if self.market_full:
                logger.info(
                    "TradeService: Markt ist voll oder API blockiert. Beende Marktangebote für diesen Zyklus."
                )
                break

            product = self.stock_service.get_product(pid)
            if not product:
                continue

            # Verkaufe keine normalen Produkte (Kategorie "v") auf dem Markt
            if (product.category == "v" and not self.config.sell_category_v) or (
                self.config.exclude_categories and product.category in self.config.exclude_categories
            ):
                logger.debug(
                    f"TradeService: Überspringe PID {pid} ('{product.name}'), "
                    f"da Kategorie '{product.category}' vom Marktverkauf ausgeschlossen ist."
                )
                continue

            rule = rule_map.get(pid)
            min_reserve = rule.min_reserve if rule else self.config.default_reserve
            batch_size = rule.sell_batch if rule else 100
            target_price = rule.target_price if rule else None

            # Only sell surplus in batches
            surplus = product.amount - min_reserve
            if surplus < batch_size:
                continue

            # Check credit again in loop
            if self.stock_service.credits_kt < self.config.min_credit_kt:
                logger.warning("TradeService: Mindestguthaben erreicht während des Einstellens.")
                break

            sell_amount = surplus
            price = await self.determine_sell_price(pid, target_price=target_price)
            if self.market_full:
                logger.info(
                    "TradeService: Preisfindung fehlgeschlagen (Markt-API fehlerhaft). Breche Marktverkäufe ab."
                )
                break

            success = await self.create_offer(pid, sell_amount, price)
            if success:
                offers_created += 1
            elif self.market_full:
                logger.info(
                    "TradeService: Markt voll oder API-Fehler nach Angebot. Beende Marktverkäufe für diesen Zyklus."
                )
                break

        return offers_created

    async def serve(self) -> dict[str, Any]:
        """Run TradeService cycle."""
        if not self.config.enabled:
            return {"enabled": False, "offers_created": 0}

        logger.info("---------- TradeService: Starte Zyklus ----------")
        offers_created = await self.sell_surplus()
        return {"enabled": True, "offers_created": offers_created}
