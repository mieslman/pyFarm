from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.auth import get_current_user
from app.worker.scheduler import worker_scheduler

plants_router = APIRouter(prefix="/plants", tags=["Plants & Products"])


def _format_product(p: Any) -> dict[str, Any]:
    """Format Product model to dictionary with camelCase aliases for dashboard compatibility."""
    return {
        "pid": p.pid,
        "name": p.name,
        "price": p.price,
        "category": p.category,
        "amount": p.amount,
        "tmpAmount": p.tmp_amount,
        "totalAmount": p.total_amount,
    }


@plants_router.get("", response_model=list[dict[str, Any]])
async def get_plants(
    category: str | None = Query(default=None, description="Kategorie-Filter (z.B. 'v', 't', 'ex')"),
    search: str | None = Query(default=None, description="Suchbegriff für Produktname"),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Retrieve catalog products with current stock amounts."""
    stock = worker_scheduler.last_stock_service
    if not stock or not stock.products:
        return []

    products = list(stock.products.values())
    if category:
        products = [p for p in products if p.category == category]
    if search:
        s_lower = search.lower()
        products = [p for p in products if s_lower in p.name.lower()]

    return [_format_product(p) for p in products]


@plants_router.get("/{pid}", response_model=dict[str, Any])
async def get_plant_by_id(
    pid: int,
    _: dict[str, Any] = Depends(get_current_user),
):
    """Retrieve single product by PID."""
    stock = worker_scheduler.last_stock_service
    if not stock:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lager nicht initialisiert.")

    product = stock.get_product(pid)
    if not product:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Produkt mit PID {pid} nicht gefunden.",
        )

    return _format_product(product)
