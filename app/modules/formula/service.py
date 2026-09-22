"""Formula Dealer (Bauplan- & Formel-Händler) service for City 2 and Field Powerups.

Handles:
1. Activating field powerups stored in the rack (activatepowerup)
2. Purchasing missing recipes/formulas at the dealer in Teichlingen (City 2)
"""

from typing import Any

from loguru import logger

from app.config import FormulaDealerConfig
from app.core.client import MFFGameClient, UpstreamAPIError
from app.services.stock_service import StockService


def bin_amount(amount: int) -> int:
    """Bin given formula amount to valid package sizes (multiples of 5, 10, or 25)."""
    if amount <= 0:
        return 0
    if amount % 25 == 0 or amount % 10 == 0 or amount % 5 == 0:
        return amount
    if amount < 5:
        return 5
    if amount < 10:
        return 10
    if amount < 25:
        return 25
    # Round up to next multiple of 25
    return ((amount + 24) // 25) * 25


class FormulaDealerService:
    """Automates powerup ignition and formula stock replenishments."""

    def __init__(
        self,
        client: MFFGameClient,
        stock_service: StockService | None = None,
        config: FormulaDealerConfig | None = None,
    ) -> None:
        self.client = client
        self.stock_service = stock_service
        self.config = config or FormulaDealerConfig()

    async def activate_powerups(self, powerups_data: dict[str, Any]) -> int:
        """Activate powerup recipes from rack on farm fields."""
        if not self.config.auto_activate_powerups:
            return 0

        rack = powerups_data.get("rack", {})
        if not isinstance(rack, dict) or not rack:
            return 0

        activated = 0
        for pid_str, item in rack.items():
            if not isinstance(item, dict):
                continue

            # Amount in rack
            amount_str = item.get("rack", 0)
            try:
                amount = int(amount_str)
            except (ValueError, TypeError):
                continue

            if amount <= 0:
                continue

            # Identify recipe name and pid
            name = str(item.get("2") or item.get("name") or f"PID {pid_str}")
            pid = int(item.get("0") or item.get("pid") or pid_str)

            # Filter by required_formulas if set
            if self.config.required_formulas and name not in self.config.required_formulas:
                continue

            logger.info(f"FormulaDealer: Zünde Powerup '{name}' (PID {pid}, Vorrat: {amount})...")
            # Upjers allows activating multiple instances
            for _ in range(amount):
                try:
                    res = await self.client.api_call(
                        "farm",
                        {
                            "mode": "activatepowerup",
                            "farm": 1,
                            "position": 1,
                            "id": pid,
                            "formula": pid,
                        },
                    )
                    datablock = res.get("datablock", [])
                    if isinstance(datablock, list) and len(datablock) > 0 and datablock[0] == 1:
                        activated += 1
                        logger.info(f"FormulaDealer: Powerup '{name}' erfolgreich gezündet.")
                        if self.stock_service and "updateblock" in res:
                            self.stock_service.update(res)
                    else:
                        logger.warning(f"FormulaDealer: Unerwartete Antwort bei Powerup '{name}': {res}")
                        break
                except UpstreamAPIError as e:
                    logger.warning(f"FormulaDealer: Fehler beim Zünden von Powerup '{name}': {e}")
                    break

        return activated

    async def buy_missing_formulas(self) -> dict[str, int]:
        """Check dealer offers in City 2 and buy formulas if below formula_min."""
        if not self.config.auto_buy_formulas:
            return {}

        try:
            res = await self.client.api_call("city", {"mode": "initformuladealer", "city": 2})
        except UpstreamAPIError as e:
            logger.warning(f"FormulaDealer: Konnte Händlerdaten in Stadt 2 nicht abrufen: {e}")
            return {}

        datablock = res.get("datablock", [])
        if not isinstance(datablock, list) or len(datablock) < 3:
            logger.warning(f"FormulaDealer: Ungültige Datenstruktur von initformuladealer: {datablock}")
            return {}

        offers = datablock[1]
        player_stock = datablock[2]

        if not isinstance(offers, list) or not isinstance(player_stock, list):
            return {}

        # Build player stock map: fid -> amount
        stock_map: dict[int, int] = {}
        for entry in player_stock:
            if isinstance(entry, dict) and "fid" in entry:
                try:
                    stock_map[int(entry["fid"])] = int(entry.get("amount", 0))
                except (ValueError, TypeError):
                    pass

        bought: dict[str, int] = {}
        target_formulas = set(self.config.required_formulas) if self.config.required_formulas else None

        for offer in offers:
            if not isinstance(offer, dict):
                continue

            fid = int(offer.get("0") or offer.get("fid") or 0)
            name = str(offer.get("2") or offer.get("name") or "")

            if not fid:
                continue

            # Check if this formula is in target list
            if target_formulas and name not in target_formulas:
                continue

            current_stock = stock_map.get(fid, 0)
            if current_stock < self.config.formula_min:
                missing = self.config.formula_min - current_stock
                amount_to_buy = bin_amount(missing)
                if amount_to_buy <= 0:
                    continue

                logger.info(
                    f"FormulaDealer: Kaufe {amount_to_buy}x Bauplan '{name}' (Bestand: {current_stock}, Min: {self.config.formula_min})..."
                )
                try:
                    buy_res = await self.client.api_call(
                        "city",
                        {
                            "mode": "buyformula",
                            "city": 2,
                            "formula": fid,
                            "amount": amount_to_buy,
                        },
                    )
                    datablock_buy = buy_res.get("datablock", [])
                    if isinstance(datablock_buy, list) and len(datablock_buy) > 0 and datablock_buy[0] == 1:
                        bought[name] = amount_to_buy
                        logger.info(f"FormulaDealer: {amount_to_buy}x Bauplan '{name}' erfolgreich gekauft.")
                        if self.stock_service and "updateblock" in buy_res:
                            self.stock_service.update(buy_res)
                    else:
                        logger.warning(
                            f"FormulaDealer: Kauf von {amount_to_buy}x '{name}' fehlgeschlagen: {buy_res}"
                        )
                except UpstreamAPIError as e:
                    logger.warning(f"FormulaDealer: Fehler beim Kauf von '{name}': {e}")

        return bought

    async def run_cycle(self, farm_data: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute full formula dealer cycle: activate powerups, buy missing formulas."""
        if not self.config.enabled:
            return {"enabled": False}

        results: dict[str, Any] = {
            "powerups_activated": 0,
            "formulas_bought": {},
        }

        # 1. Activate ready powerups
        powerups_data = {}
        if farm_data:
            powerups_data = (
                farm_data.get("updateblock", {}).get("farms", {}).get("powerups", {})
            )

        if not powerups_data:
            try:
                res = await self.client.api_call("farm", {"mode": "getfarms", "farm": 1, "position": 0})
                powerups_data = (
                    res.get("updateblock", {}).get("farms", {}).get("powerups", {})
                )
            except UpstreamAPIError as e:
                logger.warning(f"FormulaDealer: Konnte Powerup-Status nicht abrufen: {e}")

        if powerups_data:
            results["powerups_activated"] = await self.activate_powerups(powerups_data)

        # 2. Buy missing formulas
        results["formulas_bought"] = await self.buy_missing_formulas()

        return results
