from typing import Any

from fastapi import APIRouter, Body, Depends
from loguru import logger

from app.config import settings
from app.core.auth import get_current_user
from app.core.settings_manager import settings_manager
from app.worker.scheduler import worker_scheduler

forestry_router = APIRouter(prefix="/forestry", tags=["Forestry"])


@forestry_router.get("/stock", response_model=list[dict[str, Any]])
async def get_forestry_stock(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve wood and furniture stock from forestry module."""
    f_svc = worker_scheduler.last_forestry_service
    if not f_svc or not f_svc.forestry_stock:
        return []

    return [
        {
            "pid": p.pid,
            "name": p.name,
            "amount": p.amount,
            "category": p.category,
        }
        for p in f_svc.forestry_stock.values()
    ]


@forestry_router.get("/orders", response_model=dict[str, Any])
async def get_forestry_orders(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve current forestry automation configuration."""
    return settings.forestry.model_dump()


@forestry_router.put("/orders")
async def update_forestry_orders(
    payload: dict[str, Any] = Body(...),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Update forestry automation settings."""
    if "enabled" in payload:
        settings.forestry.enabled = bool(payload["enabled"])
    if "autoCut" in payload or "auto_cut" in payload:
        settings.forestry.auto_cut = bool(payload.get("autoCut", payload.get("auto_cut")))
    if "autoPlant" in payload or "auto_plant" in payload:
        settings.forestry.auto_plant = bool(payload.get("autoPlant", payload.get("auto_plant")))
    if "autoProduce" in payload or "auto_produce" in payload:
        settings.forestry.auto_produce = bool(payload.get("autoProduce", payload.get("auto_produce")))
    if "serveFarmis" in payload or "serve_farmis" in payload:
        settings.forestry.serve_farmis = bool(payload.get("serveFarmis", payload.get("serve_farmis")))

    settings_manager.save()
    logger.info(f"API: Forstwirtschafts-Konfiguration aktualisiert: {settings.forestry}")
    return {"status": "ok", "forestry_config": settings.forestry.model_dump()}
