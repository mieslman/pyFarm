from typing import Any

from fastapi import APIRouter, Body, Depends
from loguru import logger

from app.config import settings
from app.core.auth import get_current_user
from app.core.settings_manager import settings_manager
from app.modules.foodworld.models import FoodworldSummary
from app.worker.scheduler import worker_scheduler

foodworld_router = APIRouter(prefix="/foodworld", tags=["Foodworld"])


@foodworld_router.get("", response_model=dict[str, Any])
async def get_foodworld_status(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve current state and live summary of the Foodworld restaurant and kitchens."""
    fw_svc = getattr(worker_scheduler, "last_foodworld_service", None)
    if not fw_svc:
        summary = FoodworldSummary(
            enabled=settings.foodworld.enabled,
        )
        return summary.model_dump()

    return fw_svc.get_summary().model_dump()


@foodworld_router.get("/settings", response_model=dict[str, Any])
async def get_foodworld_settings(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve current Foodworld automation settings."""
    return settings.foodworld.model_dump()


@foodworld_router.put("/settings")
async def update_foodworld_settings(
    payload: dict[str, Any] = Body(...),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Update Foodworld automation settings."""
    if "enabled" in payload:
        settings.foodworld.enabled = bool(payload["enabled"])
    if "auto_cook" in payload or "autoCook" in payload:
        settings.foodworld.auto_cook = bool(payload.get("auto_cook", payload.get("autoCook")))
    if "auto_seat_guests" in payload or "autoSeatGuests" in payload:
        settings.foodworld.auto_seat_guests = bool(
            payload.get("auto_seat_guests", payload.get("autoSeatGuests"))
        )
    if "auto_cash_tables" in payload or "autoCashTables" in payload:
        settings.foodworld.auto_cash_tables = bool(
            payload.get("auto_cash_tables", payload.get("autoCashTables"))
        )
    if "auto_buy_ingredients" in payload or "autoBuyIngredients" in payload:
        settings.foodworld.auto_buy_ingredients = bool(
            payload.get("auto_buy_ingredients", payload.get("autoBuyIngredients"))
        )
    if "dish_reserve_buffer" in payload or "dishReserveBuffer" in payload:
        settings.foodworld.dish_reserve_buffer = int(
            payload.get("dish_reserve_buffer", payload.get("dishReserveBuffer"))
        )
    if "market_export_enabled" in payload or "marketExportEnabled" in payload:
        settings.foodworld.market_export_enabled = bool(
            payload.get("market_export_enabled", payload.get("marketExportEnabled"))
        )
    if "only_empty_market" in payload or "onlyEmptyMarket" in payload:
        settings.foodworld.only_empty_market = bool(
            payload.get("only_empty_market", payload.get("onlyEmptyMarket"))
        )

    settings_manager.save()
    logger.info(f"API: Foodworld-Einstellungen aktualisiert: {settings.foodworld.model_dump()}")
    return {"status": "ok", "settings": settings.foodworld.model_dump()}
