from typing import Any
from loguru import logger

from app.core.client import MFFGameClient
from app.modules.spicehouse.models import SpiceCustomer
from app.services.stock_service import StockService


class SpiceCustomerService:
    """Manages customer requests (Farmis) at the Gewürzhaus."""

    def __init__(self, client: MFFGameClient, farm_id: int = 10):
        self.client = client
        self.farm_id = farm_id
        self.customers: dict[int, SpiceCustomer] = {}

    def update(self, customers_data: dict[str, Any] | None):
        """Update customer list from spicehouse_init response."""
        self.customers = {}
        if not customers_data or not isinstance(customers_data, dict):
            return

        for slot_str, c_data in customers_data.items():
            if str(slot_str).isdigit() and isinstance(c_data, dict):
                slot_nr = int(slot_str)
                c_id = str(c_data.get("id", ""))
                raw_reqs = c_data.get("data", {})
                reqs: dict[int, int] = {}
                if isinstance(raw_reqs, dict):
                    for p_str, amt in raw_reqs.items():
                        if str(p_str).isdigit():
                            try:
                                reqs[int(p_str)] = int(amt)
                            except (ValueError, TypeError):
                                pass

                reward = c_data.get("reward", {})
                reward_pts = int(reward.get("points", 0)) if isinstance(reward, dict) else 0
                reward_money = float(reward.get("money", 0.0)) if isinstance(reward, dict) else 0.0
                reward_streuer = int(reward.get("spicehouse_points", 0)) if isinstance(reward, dict) else 0

                self.customers[slot_nr] = SpiceCustomer(
                    id=c_id,
                    slot=slot_nr,
                    data=reqs,
                    reward_points=reward_pts,
                    reward_money=reward_money,
                    reward_spicehouse_points=reward_streuer,
                )

    async def collect(self, stock_service: StockService) -> int:
        """Serve customers whose spice demands are fully available in Farm 10 stock."""
        served_count = 0

        for slot_nr, customer in list(self.customers.items()):
            if not customer.data:
                continue

            # Check if all products are satisfied in Farm 10 rack
            can_satisfy = True
            for pid, needed_amt in customer.data.items():
                avail = stock_service.get_farm_amount(self.farm_id, pid)
                if avail < needed_amt:
                    can_satisfy = False
                    break

            if can_satisfy:
                logger.info(
                    f"Gewürzhaus: Bediente Kunde an Slot {slot_nr} "
                    f"(Belohnung: {customer.reward_money:.2f} kT, {customer.reward_spicehouse_points} Streuer)..."
                )
                try:
                    res = await self.client.api_call(
                        "farm",
                        {"mode": "spicehouse_accept_customer", "slot": slot_nr},
                    )
                    datablock = res.get("datablock")
                    success = datablock == 1 or datablock == [1] or (isinstance(datablock, list) and 1 in datablock)
                    if success:
                        logger.info(
                            f"Gewürzhaus: Kunde an Slot {slot_nr} erfolgreich bedient."
                        )
                        for pid, needed_amt in customer.data.items():
                            stock_service.deduct_stock(pid, needed_amt, farm_id=self.farm_id)
                        del self.customers[slot_nr]
                        served_count += 1
                except Exception as e:
                    logger.warning(f"Gewürzhaus: Fehler beim Bedienen von Kunde {slot_nr}: {e}")

        return served_count
