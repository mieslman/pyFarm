"""MarketFarmis service for serving flower market customers and registering demand."""

from loguru import logger

from app.core.client import MFFGameClient
from app.modules.farmersmarket.models import CartItem, MarketFarmi
from app.modules.farmersmarket.order_manager import FlowerOrderManager
from app.services.stock_service import StockService


class MarketFarmisService:
    """Evaluates customer orders at the flower market, serves eligible Farmis,

    and passes unfulfilled arrangement orders to the FlowerOrderManager.
    """

    def __init__(self, client: MFFGameClient, stock_service: StockService | None = None) -> None:
        self.client = client
        self.stock_service = stock_service
        self.farmis: list[MarketFarmi] = []
        self.total_served: int = 0

    def update(self, farmersmarket_data: dict) -> None:
        """Parse waiting customers from the farmersmarket data block."""
        farmis_raw = farmersmarket_data.get("farmis", [])
        if not isinstance(farmis_raw, list):
            return

        parsed_farmis: list[MarketFarmi] = []
        for f_data in farmis_raw:
            if not isinstance(f_data, dict):
                continue

            f_id = str(f_data.get("id", ""))
            price = int(f_data.get("price", 0) or 0)
            points = int(f_data.get("points", 0) or 0)
            status = int(f_data.get("status", 0) or 0)

            cart_items: list[CartItem] = []
            for item in f_data.get("cart", []):
                if isinstance(item, dict):
                    cart_items.append(
                        CartItem(
                            pid=int(item.get("pid", 0) or 0),
                            amount=int(item.get("amount", 0) or 0),
                        )
                    )

            parsed_farmis.append(
                MarketFarmi(
                    id=f_id,
                    price=price,
                    points=points,
                    cart=cart_items,
                    status=status,
                )
            )

        self.farmis = parsed_farmis
        logger.debug(f"MarketFarmisService: {len(self.farmis)} Kunden am Blumenmarkt erfasst.")

    async def serve_and_collect_orders(self, order_manager: FlowerOrderManager) -> int:
        """Examine waiting farmis, record arrangement demands, and serve ready customers."""
        active_farmis = [f for f in self.farmis if f.status == 0]
        if not active_farmis:
            return 0

        served_count = 0
        for farmi in active_farmis:
            can_serve = True

            for item in farmi.cart:
                in_stock = self.stock_service.get_amount(item.pid) if self.stock_service else 0
                prod_info = self.stock_service.products.get(item.pid) if self.stock_service else None
                category = getattr(prod_info, "category", "")

                # If missing farm crops (category 'v'), attempt safe grasping from market/dealer
                if in_stock < item.amount and category == "v" and self.stock_service:
                    missing = item.amount - in_stock
                    try:
                        grasped = await self.stock_service.grasp_products([{"pid": item.pid, "amount": missing}])
                        if grasped:
                            in_stock = self.stock_service.get_amount(item.pid)
                    except Exception as e:  # noqa: BLE001
                        logger.debug(f"Farmis: Grasping für PID {item.pid} fehlgeschlagen: {e}")

                if in_stock < item.amount:
                    can_serve = False
                    # Check if category is flower arrangement ('fla')
                    if category == "fla" or 200 <= item.pid <= 230:
                        missing = item.amount - in_stock
                        order_manager.add_order(item.pid, missing)

            if can_serve:
                logger.info(
                    f"Blumenmarkt-Farmis: Bedient Farmi #{farmi.id} "
                    f"({farmi.price} kT, {farmi.points} Punkte)..."
                )
                try:
                    res = await self.client.api_call(
                        "farm",
                        {
                            "mode": "handleflowerfarmi",
                            "farm": 1,
                            "position": 1,
                            "id": farmi.id,
                            "farmi": farmi.id,
                            "status": 1,
                        },
                    )
                    served_count += 1
                    self.total_served += 1
                    farmi.status = 1
                    if "updateblock" in res and self.stock_service:
                        self.stock_service.update(res)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Blumenmarkt-Farmis: Fehler beim Bedienen von Farmi #{farmi.id}: {e}")

        return served_count
