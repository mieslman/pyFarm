from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from loguru import logger

from app.config import settings
from app.core.auth import get_current_user
from app.core.settings_manager import settings_manager
from app.worker.scheduler import worker_scheduler

stall_router = APIRouter(prefix="/stall", tags=["Obststand / Marktbude"])


@stall_router.get("", response_model=dict[str, Any])
async def get_stall_status(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve current Obststand / Marktbude status (stands, display slots, rewards)."""
    st_svc = worker_scheduler.last_stall_service
    summary = st_svc.get_summary().model_dump() if st_svc else {
        "active": False,
        "stalls_count": 0,
        "total_slots": 0,
        "filled_slots": 0,
        "rewards_ready_count": 0,
        "last_updated": None,
    }

    snapshot_data = st_svc.snapshot.model_dump() if (st_svc and st_svc.snapshot) else None

    return {
        "config": settings.stall.model_dump(),
        "summary": summary,
        "snapshot": snapshot_data,
    }


@stall_router.get("/settings", response_model=dict[str, Any])
async def get_stall_settings(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve current Obststand automation settings."""
    return settings.stall.model_dump()


@stall_router.put("/settings", response_model=dict[str, Any])
async def update_stall_settings(
    payload: dict[str, Any] = Body(...),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Update Obststand automation settings."""
    cfg = settings.stall
    if "enabled" in payload:
        cfg.enabled = bool(payload["enabled"])
    if "auto_clear_depleted" in payload:
        cfg.auto_clear_depleted = bool(payload["auto_clear_depleted"])
    if "clear_threshold_percent" in payload:
        try:
            cfg.clear_threshold_percent = float(payload["clear_threshold_percent"])
        except (ValueError, TypeError):
            pass
    if "auto_fill_slots" in payload:
        cfg.auto_fill_slots = bool(payload["auto_fill_slots"])
    if "auto_collect_reward" in payload:
        cfg.auto_collect_reward = bool(payload["auto_collect_reward"])
    if "min_stock_reserve" in payload:
        try:
            cfg.min_stock_reserve = int(payload["min_stock_reserve"])
        except (ValueError, TypeError):
            pass

    settings_manager.save()
    logger.info(f"Obststand: Konfiguration aktualisiert: {cfg.model_dump()}")
    return {
        "status": "success",
        "message": "Obststand-Einstellungen erfolgreich gespeichert.",
        "config": cfg.model_dump(),
    }


@stall_router.post("/action/collect", response_model=dict[str, Any])
async def action_collect_rewards(_: dict[str, Any] = Depends(get_current_user)):
    """Trigger manual collection of ready stall rewards."""
    st_svc = worker_scheduler.last_stall_service
    if not st_svc:
        raise HTTPException(
            status_code=400,
            detail="Obststand-Service ist aktuell nicht initialisiert. Bitte erst einen Zyklus ausführen.",
        )

    collected_count = await st_svc.collect_rewards()
    return {
        "status": "success",
        "collected_rewards": collected_count,
        "summary": st_svc.get_summary().model_dump(),
    }


@stall_router.post("/action/fill", response_model=dict[str, Any])
async def action_fill_slots(_: dict[str, Any] = Depends(get_current_user)):
    """Trigger manual replenishment of free display slots with available exotic fruits."""
    st_svc = worker_scheduler.last_stall_service
    if not st_svc:
        raise HTTPException(
            status_code=400,
            detail="Obststand-Service ist aktuell nicht initialisiert. Bitte erst einen Zyklus ausführen.",
        )

    stock_svc = worker_scheduler.last_stock_service
    filled_count = await st_svc.fill_free_slots(stock_service=stock_svc)
    return {
        "status": "success",
        "filled_slots": filled_count,
        "summary": st_svc.get_summary().model_dump(),
    }


@stall_router.post("/action/clear", response_model=dict[str, Any])
async def action_clear_depleted_slots(_: dict[str, Any] = Depends(get_current_user)):
    """Trigger manual clearing of depleted slots under the threshold."""
    st_svc = worker_scheduler.last_stall_service
    if not st_svc:
        raise HTTPException(
            status_code=400,
            detail="Obststand-Service ist aktuell nicht initialisiert. Bitte erst einen Zyklus ausführen.",
        )

    stock_svc = worker_scheduler.last_stock_service
    cleared_count = await st_svc.clear_depleted_slots(stock_service=stock_svc)
    return {
        "status": "success",
        "cleared_slots": cleared_count,
        "summary": st_svc.get_summary().model_dump(),
    }
