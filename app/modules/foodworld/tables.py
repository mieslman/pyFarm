"""Table, chair, and customer seating service for Foodworld."""

from loguru import logger

from app.core.client import MFFGameClient
from app.modules.foodworld.models import FoodworldFarmi, TableChair, TableGroup
from app.services.stock_service import StockService


class TableService:
    """Manages restaurant tables, seats profitable guests, and cashes out completed meals."""

    def __init__(self, client: MFFGameClient, stock_service: StockService | None = None) -> None:
        self.client = client
        self.stock_service = stock_service
        self.tables: dict[int, TableGroup] = {}
        self.farmis: list[FoodworldFarmi] = []

    def update(self, datablock: dict) -> None:
        """Parse tables, chairs, and waiting farmis from upstream datablock."""
        # 1. Parse tables & chairs
        raw_tables = datablock.get("tables", [])
        parsed_tables: dict[int, TableGroup] = {}

        if isinstance(raw_tables, list):
            tables_items = list(enumerate(raw_tables))
        elif isinstance(raw_tables, dict):
            tables_items = [(int(k), v) for k, v in raw_tables.items()]
        else:
            tables_items = []

        for t_idx, t_data in tables_items:
            if not isinstance(t_data, dict):
                continue
            block = int(t_data.get("block", 0) or 0)
            locked = int(t_data.get("locked", 0) or 0)

            chairs: dict[int, TableChair] = {}
            raw_chairs = t_data.get("chairs", {})
            if isinstance(raw_chairs, dict):
                for c_id_str, c_data in raw_chairs.items():
                    try:
                        c_id = int(c_id_str)
                        if isinstance(c_data, dict) and c_data:
                            f_id = str(c_data.get("id")) if c_data.get("id") else None
                            ready = int(c_data.get("ready", 0) or 0)
                            remain = int(c_data.get("remain", 0) or 0)
                        else:
                            f_id = None
                            ready = 0
                            remain = 0
                        chairs[c_id] = TableChair(
                            chair_id=c_id,
                            farmi_id=f_id,
                            ready=ready,
                            remain=remain,
                        )
                    except (ValueError, TypeError) as e:
                        logger.debug(f"TableService: Konnte Stuhl {c_id_str} nicht parsen: {e}")

            parsed_tables[t_idx] = TableGroup(
                table_id=t_idx,
                chairs=chairs,
                block=block,
                locked=locked,
            )

        self.tables = parsed_tables

        # 2. Parse farmis
        raw_farmis = datablock.get("farmis", [])
        parsed_farmis: list[FoodworldFarmi] = []

        if isinstance(raw_farmis, list):
            for f_data in raw_farmis:
                if not isinstance(f_data, dict):
                    continue
                f_id = str(f_data.get("id", ""))
                price = int(f_data.get("price", 0) or 0)
                points = int(f_data.get("points", 0) or 0)
                status = int(f_data.get("status", 0) or 0)

                cart: dict[int, int] = {}
                raw_prods = f_data.get("products") or f_data.get("cart") or {}
                for pid_str, amt_val in raw_prods.items():
                    if str(pid_str).isdigit():
                        cart[int(pid_str)] = int(amt_val)

                parsed_farmis.append(
                    FoodworldFarmi(
                        id=f_id,
                        price=price,
                        points=points,
                        status=status,
                        cart=cart,
                    )
                )

        self.farmis = parsed_farmis
        logger.debug(
            f"TableService: {len(self.tables)} Tische und {len(self.farmis)} Farmis erfasst."
        )

    async def cash_tables(self) -> tuple[int, float]:
        """Cash out all ready guests at tables, collecting price + tip + bonus in kT."""
        cashed_count = 0
        revenue_collected = 0.0

        for table in self.tables.values():
            for chair in table.ready_chairs:
                logger.info(
                    f"Foodworld-Tische: Kassiere fertigen Gast an Tisch {table.table_id}, Stuhl {chair.chair_id} ab..."
                )
                try:
                    res = await self.client.api_call(
                        "foodworld",
                        {
                            "action": "cash",
                            "id": 0,
                            "table": table.table_id,
                            "chair": chair.chair_id,
                        },
                    )
                    cashed_count += 1
                    # Extract money credited
                    transfer_info = res.get("datablock", {}).get("transfer", {})
                    if isinstance(transfer_info, dict) and "money" in transfer_info:
                        revenue_collected += float(transfer_info.get("money", 0) or 0)

                    chair.farmi_id = None
                    chair.ready = 0
                    chair.remain = 0

                    if "updateblock" in res and self.stock_service:
                        self.stock_service.update(res)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"Foodworld-Tische: Fehler beim Abkassieren von Tisch {table.table_id}, Stuhl {chair.chair_id}: {e}"
                    )

        return cashed_count, revenue_collected

    async def seat_guests(self) -> int:
        """Seat waiting guests at free chairs sorted by highest offered price."""
        waiting_farmis = [f for f in self.farmis if f.status == 0]
        if not waiting_farmis:
            return 0

        # Sort by highest offered price descending (best guests first)
        waiting_farmis.sort(key=lambda f: f.price, reverse=True)
        seated_count = 0

        for farmi in waiting_farmis:
            # Check if all required food products are in stock
            if not self._is_cart_in_stock(farmi.cart):
                continue

            # Find a free chair at an unlocked table
            target_table: TableGroup | None = None
            target_chair: TableChair | None = None

            for table in self.tables.values():
                if table.is_unlocked:
                    free_chairs = table.free_chairs
                    if free_chairs:
                        target_table = table
                        target_chair = free_chairs[0]
                        break

            if not target_table or not target_chair:
                # No free chairs available in restaurant
                break

            logger.info(
                f"Foodworld-Tische: Platziere Gast #{farmi.id} ({farmi.price} kT) "
                f"an Tisch {target_table.table_id}, Stuhl {target_chair.chair_id}..."
            )
            try:
                res = await self.client.api_call(
                    "foodworld",
                    {
                        "action": "dropped",
                        "id": farmi.id,
                        "table": target_table.table_id,
                        "chair": target_chair.chair_id,
                    },
                )
                seated_count += 1
                farmi.status = 1
                target_chair.farmi_id = farmi.id
                target_chair.remain = 25000  # Default ~7h duration
                target_chair.ready = 0

                if "updateblock" in res and self.stock_service:
                    self.stock_service.update(res)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"Foodworld-Tische: Fehler beim Platzieren von Gast #{farmi.id}: {e}"
                )

        return seated_count

    def get_demanded_cart(self) -> dict[int, int]:
        """Aggregate product demands across all waiting farmis (status == 0).

        Farmis are ordered by price descending so highest profitability requests are served first.
        Returns: {pid: total_needed_quantity}
        """
        demands: dict[int, int] = {}
        waiting = [f for f in self.farmis if f.status == 0]
        # Sort by offered reward descending (most profitable guests first)
        waiting.sort(key=lambda f: f.price, reverse=True)

        for farmi in waiting:
            for pid, amt in farmi.cart.items():
                demands[pid] = demands.get(pid, 0) + amt

        return demands

    def get_demanded_pids(self) -> list[int]:
        """Return distinct product PIDs requested by waiting customers."""
        return list(self.get_demanded_cart().keys())

    def _is_cart_in_stock(self, cart: dict[int, int]) -> bool:
        """Check if all products in the customer's cart are available in inventory."""
        if not self.stock_service:
            return True
        for pid, needed_amt in cart.items():
            if self.stock_service.get_amount(pid) < needed_amt:
                return False
        return True
