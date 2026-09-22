from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from loguru import logger

from app.config import settings
from app.core.auth import get_current_user
from app.core.client import MFFGameClient
from app.core.settings_manager import settings_manager
from app.modules.insecthotel import InsectHotelService
from app.worker.scheduler import worker_scheduler

insecthotel_router = APIRouter(prefix="/insecthotel", tags=["Insektenhotel"])


@insecthotel_router.get("", response_model=dict[str, Any])
async def get_insecthotel_status(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve current Insect Hotel status (populations, satisfaction, feeding slots, checkout)."""
    ih_svc = worker_scheduler.last_insecthotel_service
    summary = ih_svc.get_summary().model_dump() if ih_svc else {
        "active": False,
        "hotel_id": "",
        "total_population": 0,
        "slots_count": 0,
        "stock_slots_count": 0,
        "checkout_money": 0.0,
        "checkout_points": 0,
        "checkout_money_limit": 0.0,
        "checkout_points_limit": 0,
        "last_updated": None,
    }

    snapshot_data = ih_svc.snapshot.model_dump() if (ih_svc and ih_svc.snapshot) else None

    return {
        "config": settings.insecthotel.model_dump(),
        "summary": summary,
        "snapshot": snapshot_data,
    }


@insecthotel_router.get("/settings", response_model=dict[str, Any])
async def get_insecthotel_settings(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve current Insect Hotel automation settings."""
    return settings.insecthotel.model_dump()


@insecthotel_router.put("/settings", response_model=dict[str, Any])
async def update_insecthotel_settings(
    payload: dict[str, Any] = Body(...),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Update Insect Hotel automation settings."""
    cfg = settings.insecthotel
    if "enabled" in payload:
        cfg.enabled = bool(payload["enabled"])
    if "auto_refill_stock" in payload:
        cfg.auto_refill_stock = bool(payload["auto_refill_stock"])
    if "refill_threshold_percent" in payload:
        try:
            cfg.refill_threshold_percent = float(payload["refill_threshold_percent"])
        except (ValueError, TypeError):
            pass
    if "auto_collect_checkout" in payload:
        cfg.auto_collect_checkout = bool(payload["auto_collect_checkout"])
    if "checkout_threshold_percent" in payload:
        try:
            cfg.checkout_threshold_percent = float(payload["checkout_threshold_percent"])
        except (ValueError, TypeError):
            pass
    if "min_stock_reserve" in payload:
        try:
            cfg.min_stock_reserve = int(payload["min_stock_reserve"])
        except (ValueError, TypeError):
            pass
    if "auto_buy_feed" in payload:
        cfg.auto_buy_feed = bool(payload["auto_buy_feed"])

    settings_manager.save()
    logger.info(f"Insektenhotel: Konfiguration aktualisiert: {cfg.model_dump()}")
    return {
        "status": "success",
        "message": "Insektenhotel-Einstellungen erfolgreich gespeichert.",
        "config": cfg.model_dump(),
    }


@insecthotel_router.post("/action/checkout", response_model=dict[str, Any])
async def action_collect_checkout(
    force: bool = False,
    _: dict[str, Any] = Depends(get_current_user),
):
    """Trigger manual collection of hotel cash register."""
    ih_svc = worker_scheduler.last_insecthotel_service
    if not ih_svc:
        raise HTTPException(
            status_code=400,
            detail="Insektenhotel-Service ist aktuell nicht initialisiert. Bitte erst einen Zyklus ausführen.",
        )

    collected = await ih_svc.collect_checkout(force=force)
    return {
        "status": "success",
        "collected": collected,
        "summary": ih_svc.get_summary().model_dump(),
    }


@insecthotel_router.post("/action/refill", response_model=dict[str, Any])
async def action_refill_stock(
    force: bool = False,
    _: dict[str, Any] = Depends(get_current_user),
):
    """Trigger manual refill of hotel feeding compartments from warehouse stock."""
    ih_svc = worker_scheduler.last_insecthotel_service
    if not ih_svc:
        raise HTTPException(
            status_code=400,
            detail="Insektenhotel-Service ist aktuell nicht initialisiert. Bitte erst einen Zyklus ausführen.",
        )

    stock_svc = worker_scheduler.last_stock_service
    refilled_count = await ih_svc.refill_stock(stock_service=stock_svc, force=force)
    return {
        "status": "success",
        "refilled_slots": refilled_count,
        "summary": ih_svc.get_summary().model_dump(),
    }
