from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from loguru import logger

from app.config import settings
from app.core.auth import get_current_user
from app.core.client import MFFGameClient
from app.core.settings_manager import settings_manager
from app.modules.events import EventManager
from app.worker.scheduler import worker_scheduler

events_router = APIRouter(prefix="/events", tags=["Saisonevents"])


@events_router.get("", response_model=dict[str, Any])
async def get_events_overview(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve overview and status of all active seasonal events."""
    mgr = worker_scheduler.last_event_manager
    if mgr:
        overview = (await mgr.get_overview()).model_dump()
    else:
        overview = {
            "active_events": [],
            "calendar": None,
            "delivery": None,
            "eventgarden": None,
            "oktoberfest": None,
            "pentecost": None,
            "olympia": None,
            "last_run": None,
        }

    return {
        "config": settings.events.model_dump(),
        "overview": overview,
    }


@events_router.get("/settings", response_model=dict[str, Any])
async def get_events_settings(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve current seasonal events settings."""
    return settings.events.model_dump()


@events_router.put("/settings", response_model=dict[str, Any])
async def update_events_settings(
    payload: dict[str, Any] = Body(...),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Update seasonal events automation settings."""
    cfg = settings.events
    for key in (
        "enabled",
        "auto_detect",
        "calendar_enabled",
        "delivery_enabled",
        "eventgarden_enabled",
        "oktoberfest_enabled",
        "pentecost_enabled",
        "olympia_enabled",
    ):
        if key in payload:
            setattr(cfg, key, bool(payload[key]))

    settings_manager.save()
    logger.info(f"Saisonevents: Konfiguration aktualisiert: {cfg.model_dump()}")
    return {
        "status": "success",
        "message": "Saisonevent-Einstellungen erfolgreich gespeichert.",
        "config": cfg.model_dump(),
    }


@events_router.post("/action/run", response_model=dict[str, Any])
async def action_run_events(_: dict[str, Any] = Depends(get_current_user)):
    """Trigger manual execution of active seasonal events."""
    account = settings.account
    if not account.username or not account.password:
        raise HTTPException(
            status_code=400,
            detail="Keine Spielzugangsdaten in den Einstellungen hinterlegt.",
        )

    client = MFFGameClient(
        server=account.server,
        username=account.username,
        password=account.password,
    )
    try:
        await client.login()
        mgr = EventManager(client)
        results = await mgr.serve()
        worker_scheduler.last_event_manager = mgr
        return {
            "status": "success",
            "results": results,
            "overview": (await mgr.get_overview()).model_dump(),
        }
    finally:
        await client.logout()
