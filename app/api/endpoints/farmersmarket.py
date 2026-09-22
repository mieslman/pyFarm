from typing import Any

from fastapi import APIRouter, Body, Depends
from loguru import logger

from app.config import settings
from app.core.auth import get_current_user
from app.core.settings_manager import settings_manager
from app.modules.farmersmarket.models import FarmersMarketSummary
from app.worker.scheduler import worker_scheduler

farmersmarket_router = APIRouter(prefix="/farmersmarket", tags=["FarmersMarket"])


@farmersmarket_router.get("", response_model=dict[str, Any])
async def get_farmersmarket_status(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve current state and summary of the Dorf 2 farmers market domains."""
    fm_svc = worker_scheduler.last_farmersmarket_service
    if not fm_svc:
        summary = FarmersMarketSummary(
            enabled=settings.farmersmarket.enabled,
            pet_breed_enabled=settings.farmersmarket.pet_breed_enabled,
            pet_breed_status="Inaktiv (Konfiguration)"
            if not settings.farmersmarket.pet_breed_enabled
            else "Aktiv",
        )
        return summary.model_dump()

    return fm_svc.get_summary().model_dump()


@farmersmarket_router.get("/settings", response_model=dict[str, Any])
async def get_farmersmarket_settings(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve current farmers market automation settings."""
    return settings.farmersmarket.model_dump()


@farmersmarket_router.put("/settings")
async def update_farmersmarket_settings(
    payload: dict[str, Any] = Body(...),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Update farmers market automation settings."""
    if "enabled" in payload:
        settings.farmersmarket.enabled = bool(payload["enabled"])
    if "nursery_enabled" in payload or "nurseryEnabled" in payload:
        settings.farmersmarket.nursery_enabled = bool(
            payload.get("nursery_enabled", payload.get("nurseryEnabled"))
        )
    if "flower_area_enabled" in payload or "flowerAreaEnabled" in payload:
        settings.farmersmarket.flower_area_enabled = bool(
            payload.get("flower_area_enabled", payload.get("flowerAreaEnabled"))
        )
    if "flower_slots_enabled" in payload or "flowerSlotsEnabled" in payload:
        settings.farmersmarket.flower_slots_enabled = bool(
            payload.get("flower_slots_enabled", payload.get("flowerSlotsEnabled"))
        )
    if "farmis_enabled" in payload or "farmisEnabled" in payload:
        settings.farmersmarket.farmis_enabled = bool(
            payload.get("farmis_enabled", payload.get("farmisEnabled"))
        )
    if "pet_breed_enabled" in payload or "petBreedEnabled" in payload:
        settings.farmersmarket.pet_breed_enabled = bool(
            payload.get("pet_breed_enabled", payload.get("petBreedEnabled"))
        )
    if "max_flower_batch" in payload:
        settings.farmersmarket.max_flower_batch = int(payload["max_flower_batch"])

    # Synchronize with active service instance if present
    if worker_scheduler.last_farmersmarket_service:
        worker_scheduler.last_farmersmarket_service.config = settings.farmersmarket
        worker_scheduler.last_farmersmarket_service.pet_breed.enabled = (
            settings.farmersmarket.pet_breed_enabled
        )

    settings_manager.save()
    logger.info(f"API: Bauernmarkt-Konfiguration aktualisiert: {settings.farmersmarket}")
    return {"status": "ok", "farmersmarket_config": settings.farmersmarket.model_dump()}
