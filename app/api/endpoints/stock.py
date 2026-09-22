from typing import Any

from fastapi import APIRouter, Body, Depends
from loguru import logger

from app.config import settings
from app.core.auth import get_current_user
from app.core.settings_manager import settings_manager
from app.worker.scheduler import worker_scheduler

stock_router = APIRouter(prefix="/stock", tags=["Stock & Inventory"])


@stock_router.get("", response_model=dict[str, Any])
async def get_stock_overview(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve overview of current account balance and inventory summary."""
    stock = worker_scheduler.last_stock_service
    if not stock:
        return {
            "creditsKt": 0.0,
            "totalItems": 0,
            "productsCount": 0,
            "isInitialized": False,
        }

    total_items = sum(p.total_amount for p in stock.products.values())
    return {
        "creditsKt": stock.credits_kt,
        "totalItems": total_items,
        "productsCount": len(stock.products),
        "bufferCrops": settings.stock.buffer_crops,
        "bufferOther": settings.stock.buffer_other,
        "minCreditKt": settings.stock.min_credit_kt,
        "isInitialized": True,
    }


@stock_router.get("/orders", response_model=list[dict[str, Any]])
async def get_stock_orders(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve configured stock replenishment orders and buffer limits."""
    # Provide default / configured buffer rules
    orders = [
        {
            "category": "v",
            "name": "Feldfrüchte (Pflanzen)",
            "buffer": settings.stock.buffer_crops,
            "maxPriceFactor": settings.stock.max_price_factor_crops,
        },
        {
            "category": "other",
            "name": "Tierprodukte & Sonstiges",
            "buffer": settings.stock.buffer_other,
            "maxPriceFactor": settings.stock.max_price_factor_other,
        },
    ]
    return orders


@stock_router.put("/orders")
async def update_stock_orders(
    payload: dict[str, Any] = Body(...),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Update stock buffering and purchasing configuration."""
    if "bufferCrops" in payload:
        settings.stock.buffer_crops = int(payload["bufferCrops"])
    elif "buffer_crops" in payload:
        settings.stock.buffer_crops = int(payload["buffer_crops"])

    if "bufferOther" in payload:
        settings.stock.buffer_other = int(payload["bufferOther"])
    elif "buffer_other" in payload:
        settings.stock.buffer_other = int(payload["buffer_other"])

    if "minCreditKt" in payload:
        settings.stock.min_credit_kt = float(payload["minCreditKt"])
    elif "min_credit_kt" in payload:
        settings.stock.min_credit_kt = float(payload["min_credit_kt"])

    settings_manager.save()
    logger.info(f"API: Lagereinstellungen aktualisiert: {settings.stock}")
    return {"status": "ok", "stock_config": settings.stock.model_dump()}
