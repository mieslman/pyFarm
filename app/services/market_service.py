import time

from loguru import logger

from app.core.client import MFFGameClient
from app.core.exceptions import UpstreamAPIError
from app.models.product import MarketOffer


class MarketService:
    """Service for interacting with the MyFreeFarm player marketplace."""

    DEFAULT_CACHE_TTL: float = 60.0

    def __init__(self, client: MFFGameClient, cache_ttl: float = DEFAULT_CACHE_TTL):
        self.client = client
        self.cache_ttl = cache_ttl
        self._offers_cache: list[MarketOffer] | None = None
        self._offers_cached_at: float = 0.0

    def invalidate_cache(self) -> None:
        """Clear cached marketplace offers so the next read fetches fresh data."""
        self._offers_cache = None
        self._offers_cached_at = 0.0

    def _is_cache_valid(self) -> bool:
        return (
            self.cache_ttl > 0
            and self._offers_cache is not None
            and (time.monotonic() - self._offers_cached_at) < self.cache_ttl
        )

    async def get_offers(
        self, pid: int | None = None, *, use_cache: bool = True
    ) -> list[MarketOffer]:
        """Fetch active marketplace offers, optionally filtered by product ID, sorted by price."""
        if use_cache and self._is_cache_valid():
            all_offers = self._offers_cache or []
        else:
            data = await self.client.api_call("city", {"mode": "marketinit", "id": 0, "comp": 1})
            if not data.get("datablock") or len(data["datablock"]) < 2:
                logger.warning("Unerwartete Antwort von marketinit: kein datablock[1] gefunden.")
                return []

            raw_offers = data["datablock"][1].get("offers", [])
            all_offers = []

            for o in raw_offers:
                try:
                    offer_pid = int(o.get("p", 0))
                    all_offers.append(
                        MarketOffer(
                            offer_id=int(o["id"]),
                            pid=offer_pid,
                            amount=int(o.get("a", 0)),
                            price=float(o.get("pr", 0.0)),
                        )
                    )
                except (ValueError, KeyError) as e:
                    logger.debug(f"Überspringe ungültiges Marktangebot: {o} ({e})")

            # Sort ascending by price (cheapest offers first)
            all_offers.sort(key=lambda x: x.price)
            if self.cache_ttl > 0:
                self._offers_cache = all_offers
                self._offers_cached_at = time.monotonic()

        if pid is not None:
            return [o for o in all_offers if o.pid == pid]
        return list(all_offers)

    async def buy(self, pid: int, amount: int, max_price: float) -> int:
        """Buy cheapest available offers for product until quantity is reached or max_price exceeded."""
        if amount <= 0:
            return 0

        offers = await self.get_offers(pid=pid)
        remaining = amount
        total_bought = 0

        logger.info(f"Marktplatz: Suche {amount}x PID {pid} (Maximalpreis: {max_price:.2f} kT)...")

        for offer in offers:
            if offer.price > max_price:
                logger.debug(
                    f"Nächstes Angebot zu teuer: {offer.price:.2f} kT > Max {max_price:.2f} kT. Kauf beendet."
                )
                break

            buy_qty = min(remaining, offer.amount)
            logger.info(
                f"  Kaufe {buy_qty}x PID {pid} für {offer.price:.2f} kT/Stk (Angebot ID: {offer.offer_id})..."
            )

            try:
                res = await self.client.api_call(
                    "city",
                    {
                        "mode": "marketbuyoffer",
                        "id": offer.offer_id,
                        "pid": offer.pid,
                        "amount": buy_qty,
                        "comp": 1,
                    },
                )
            except UpstreamAPIError as e:
                logger.warning(f"Fehler beim Marktkauf von Angebot {offer.offer_id}: {e}")
                continue

            if res.get("datablock", [None])[0] == 1:
                remaining -= buy_qty
                total_bought += buy_qty
            else:
                logger.warning(f"Marktkauf abgewiesen: {res}")

            if remaining <= 0:
                break

        if total_bought > 0:
            self.invalidate_cache()

        logger.info(f"Marktplatz: Insgesamt {total_bought}x PID {pid} gekauft.")
        return total_bought
