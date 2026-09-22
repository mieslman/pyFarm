from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from loguru import logger

from app.config import settings
from app.core.auth import get_current_user
from app.core.settings_manager import settings_manager
from app.worker.scheduler import worker_scheduler

seasonpass_router = APIRouter(prefix="/seasonpass", tags=["Seasonpass"])


@seasonpass_router.get("", response_model=dict[str, Any])
async def get_seasonpass_status(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve current Seasonpass status, pending/completed tasks, points, and tiers."""
    sp_svc = worker_scheduler.last_seasonpass_service
    summary = sp_svc.get_summary() if sp_svc else {
        "active": False,
        "season_name": "",
        "points": 0,
        "remain": 0,
        "pending_tasks": [],
        "completed_tasks": [],
        "levels": {},
    }

    return {
        "config": settings.seasonpass.model_dump(),
        "summary": summary,
    }


@seasonpass_router.get("/settings", response_model=dict[str, Any])
async def get_seasonpass_settings(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve current Seasonpass automation settings."""
    return settings.seasonpass.model_dump()


@seasonpass_router.put("/settings", response_model=dict[str, Any])
async def update_seasonpass_settings(
    payload: dict[str, Any] = Body(...),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Update Seasonpass automation settings."""
    cfg = settings.seasonpass
    if "enabled" in payload:
        cfg.enabled = bool(payload["enabled"])
    if "auto_claim_rewards" in payload:
        cfg.auto_claim_rewards = bool(payload["auto_claim_rewards"])
    if "friend_unr" in payload:
        cfg.friend_unr = str(payload["friend_unr"])
    if "preferred_field_farm" in payload:
        try:
            cfg.preferred_field_farm = int(payload["preferred_field_farm"])
        except (ValueError, TypeError):
            pass
    if "preferred_field_pos" in payload:
        try:
            cfg.preferred_field_pos = int(payload["preferred_field_pos"])
        except (ValueError, TypeError):
            pass
    if "preferred_forestry_pos" in payload:
        try:
            cfg.preferred_forestry_pos = int(payload["preferred_forestry_pos"])
        except (ValueError, TypeError):
            pass

    # Propagate to active service instance if present
    if worker_scheduler.last_seasonpass_service:
        worker_scheduler.last_seasonpass_service.config.enabled = cfg.enabled
        worker_scheduler.last_seasonpass_service.config.auto_claim_rewards = cfg.auto_claim_rewards
        worker_scheduler.last_seasonpass_service.config.preferred_field_farm = cfg.preferred_field_farm
        worker_scheduler.last_seasonpass_service.config.preferred_field_pos = cfg.preferred_field_pos
        worker_scheduler.last_seasonpass_service.config.preferred_forestry_pos = cfg.preferred_forestry_pos

    settings_manager.save()
    logger.info(f"Seasonpass: Konfiguration aktualisiert: {cfg.model_dump()}")
    return {
        "status": "success",
        "message": "Seasonpass-Einstellungen erfolgreich gespeichert.",
        "config": cfg.model_dump(),
    }


@seasonpass_router.post("/action/claim", response_model=dict[str, Any])
async def action_claim_rewards(_: dict[str, Any] = Depends(get_current_user)):
    """Trigger manual collection of unlocked free tier rewards."""
    sp_svc = worker_scheduler.last_seasonpass_service
    if not sp_svc:
        raise HTTPException(
            status_code=400,
            detail="Seasonpass-Service ist aktuell nicht initialisiert. Bitte erst einen Zyklus ausführen.",
        )

    claimed = await sp_svc.claim_available_rewards()
    return {
        "status": "success",
        "claimed_count": claimed,
    }
