"""Order manager for coordinating demand for flower arrangements (Nursery / Farmis)."""

from loguru import logger


class FlowerOrderManager:
    """Manages required flower arrangements demanded by Farmis or Quests.

    Ensures that the Nursery only produces arrangements that are actively
    requested, preventing unnecessary inventory buildup or material waste.
    """

    def __init__(self) -> None:
        # Map of arrangement PID -> requested quantity
        self._orders: dict[int, int] = {}

    def add_order(self, pid: int, amount: int = 1) -> None:
        """Register a demand for a specific flower arrangement."""
        current = self._orders.get(pid, 0)
        self._orders[pid] = current + amount
        logger.debug(f"FlowerOrderManager: Bedarf registriert für PID {pid} (+{amount}, gesamt: {self._orders[pid]})")

    def get_orders(self) -> list[int]:
        """Return list of arrangement PIDs currently demanded."""
        return [pid for pid, amount in self._orders.items() if amount > 0]

    def get_demand(self, pid: int) -> int:
        """Get demanded quantity for a specific arrangement PID."""
        return self._orders.get(pid, 0)

    def consume_order(self, pid: int, amount: int = 1) -> None:
        """Decrease or remove demand after an arrangement has been crafted."""
        if pid in self._orders:
            self._orders[pid] -= amount
            if self._orders[pid] <= 0:
                del self._orders[pid]
            logger.debug(f"FlowerOrderManager: Bedarf bedient für PID {pid} (-{amount})")

    def clear(self) -> None:
        """Clear all registered orders."""
        self._orders.clear()

    @property
    def has_orders(self) -> bool:
        """Check if any arrangements are demanded."""
        return len(self._orders) > 0
