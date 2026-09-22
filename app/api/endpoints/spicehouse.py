from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from loguru import logger

from app.config import settings
from app.core.auth import get_current_user
from app.core.settings_manager import settings_manager
from app.worker.scheduler import worker_scheduler

spicehouse_router = APIRouter(prefix="/spicehouse", tags=["Gewürzhaus"])


@spicehouse_router.get("", response_model=dict[str, Any])
async def get_spicehouse_status(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve current Gewürzhaus status, oven slots, mill slots, customers, and configuration."""
    sh_svc = worker_scheduler.last_spicehouse_service
    summary = sh_svc.state.model_dump() if sh_svc else None

    return {
        "config": settings.spicehouse.model_dump(),
        "summary": summary,
    }


@spicehouse_router.get("/settings", response_model=dict[str, Any])
async def get_spicehouse_settings(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve current Gewürzhaus automation settings."""
    return settings.spicehouse.model_dump()


@spicehouse_router.put("/settings", response_model=dict[str, Any])
async def update_spicehouse_settings(
    payload: dict[str, Any] = Body(...),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Update Gewürzhaus automation settings."""
    cfg = settings.spicehouse
    if "enabled" in payload:
        cfg.enabled = bool(payload["enabled"])
    if "auto_oven" in payload:
        cfg.auto_oven = bool(payload["auto_oven"])
    if "auto_mill" in payload:
        cfg.auto_mill = bool(payload["auto_mill"])
    if "auto_customer" in payload:
        cfg.auto_customer = bool(payload["auto_customer"])
    if "min_crop_reserve" in payload:
        try:
            cfg.min_crop_reserve = int(payload["min_crop_reserve"])
        except (ValueError, TypeError):
            pass

    # Propagate to active service instance
    if worker_scheduler.last_spicehouse_service:
        worker_scheduler.last_spicehouse_service.config.enabled = cfg.enabled
        worker_scheduler.last_spicehouse_service.config.auto_oven = cfg.auto_oven
        worker_scheduler.last_spicehouse_service.config.auto_mill = cfg.auto_mill
        worker_scheduler.last_spicehouse_service.config.auto_customer = cfg.auto_customer
        worker_scheduler.last_spicehouse_service.config.min_crop_reserve = cfg.min_crop_reserve

    settings_manager.save()
    logger.info(f"Gewürzhaus: Konfiguration aktualisiert: {cfg.model_dump()}")
    return {
        "status": "success",
        "message": "Gewürzhaus-Einstellungen erfolgreich gespeichert.",
        "config": cfg.model_dump(),
    }


@spicehouse_router.post("/action/harvest", response_model=dict[str, Any])
async def action_harvest(_: dict[str, Any] = Depends(get_current_user)):
    """Trigger manual harvest of completed oven and mill slots."""
    sh_svc = worker_scheduler.last_spicehouse_service
    if not sh_svc:
        raise HTTPException(
            status_code=400,
            detail="Gewürzhaus-Service ist aktuell nicht initialisiert. Bitte erst einen Zyklus ausführen.",
        )

    oven_harvested = await sh_svc.oven.harvest()
    mill_harvested = await sh_svc.mill.harvest()

    return {
        "status": "success",
        "oven_harvested": oven_harvested,
        "mills_harvested": mill_harvested,
    }
