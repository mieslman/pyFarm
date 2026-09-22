from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from loguru import logger

from app.core.auth import get_current_user
from app.core.settings_manager import SECTION_MODELS, settings_manager

config_router = APIRouter(prefix="/config", tags=["Configuration Persistence"])


@config_router.get("", response_model=dict[str, Any])
async def get_all_config(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve complete user configuration (sections + scalar fields) with persistence metadata."""
    export = settings_manager.get_export_data()
    return {
        **export,
        "_persistence": settings_manager.get_status(),
    }


@config_router.get("/status", response_model=dict[str, Any])
async def get_config_persistence_status(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve storage file path, size, and last modified timestamps."""
    return settings_manager.get_status()


@config_router.put("", response_model=dict[str, Any])
async def update_bulk_config(
    payload: dict[str, Any] = Body(...),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Update one or multiple configuration sections and/or scalar fields, then persist to disk."""
    from app.core.settings_manager import SCALAR_FIELDS

    updated_sections: list[str] = []
    updated_scalars: list[str] = []
    errors: dict[str, str] = {}

    for key, value in payload.items():
        if key.startswith("_"):
            continue
        if key in SECTION_MODELS:
            if not isinstance(value, dict):
                errors[key] = f"Sektion '{key}' muss ein JSON-Objekt sein."
                continue
            try:
                settings_manager.update_section(key, value)
                updated_sections.append(key)
            except Exception as e:
                errors[key] = str(e)
        elif key in SCALAR_FIELDS:
            try:
                from app.config import settings as _settings

                target_type = type(getattr(_settings, key))
                setattr(_settings, key, target_type(value))
                updated_scalars.append(key)
            except Exception as e:
                errors[key] = str(e)

    if errors:
        raise HTTPException(
            status_code=400,
            detail={"message": "Fehler beim Aktualisieren der Konfiguration", "errors": errors},
        )

    # Persist everything (sections + scalars) in one write
    saved = settings_manager.save_settings()

    logger.info(
        f"API: Konfiguration aktualisiert: Sektionen={updated_sections}, Skalare={updated_scalars}"
    )
    return {
        "status": "success",
        "message": f"{len(updated_sections)} Sektion(en), {len(updated_scalars)} Skalar(e) gespeichert.",
        "updated_sections": updated_sections,
        "updated_scalars": updated_scalars,
        "persistence": settings_manager.get_status(),
        "config": saved,
    }


@config_router.post("/reload", response_model=dict[str, Any])
async def reload_config_from_disk(_: dict[str, Any] = Depends(get_current_user)):
    """Force reload configuration from the on-disk user_config.json file."""
    data = settings_manager.load_settings()
    logger.info("API: Konfiguration manuell von Festplatte neu geladen.")
    return {
        "status": "success",
        "message": "Konfiguration erfolgreich von Festplatte neu geladen.",
        "persistence": settings_manager.get_status(),
        "config": data,
    }
