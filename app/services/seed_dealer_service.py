from loguru import logger

from app.core.client import MFFGameClient
from app.core.exceptions import UpstreamAPIError


class SeedDealerService:
    """Service for buying seeds directly from the NPC seed merchant (Saatguthändler)."""

    def __init__(self, client: MFFGameClient):
        self.client = client

    async def buy(self, pid: int, amount: int) -> int:
        """Purchase seeds from the official NPC seed merchant at fixed system prices."""
        if amount <= 0:
            return 0

        logger.info(f"Saatguthändler: Kaufe {amount}x PID {pid}...")
        try:
            res = await self.client.api_call(
                "city",
                {
                    "mode": "shopfire",
                    "shopid": 1,
                    "cart": f"{pid},{amount}",
                },
            )
        except UpstreamAPIError as e:
            logger.error(f"Fehler beim Kauf beim Saatguthändler: {e}")
            return 0

        if res.get("datablock", [None])[0] == 1:
            logger.info(f"Saatguthändler: {amount}x PID {pid} erfolgreich gekauft.")
            return amount

        logger.warning(f"Saatguthändler: Kauf abgelehnt (Antwort: {res})")
        return 0
