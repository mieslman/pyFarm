from typing import Any

from fastapi import APIRouter, Body, Depends
from loguru import logger

from app.config import settings
from app.core.auth import get_current_user
from app.core.settings_manager import settings_manager
from app.worker.scheduler import worker_scheduler

fuelstation_router = APIRouter(prefix="/fuelstation", tags=["Fuelstation"])


@fuelstation_router.get("", response_model=dict[str, Any])
async def get_fuelstation(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve fuelstation status and automation configuration."""
    farm_svc = worker_scheduler.last_farm_service
    stock_svc = worker_scheduler.last_stock_service

    fs_states = []
    if farm_svc:
        for fs in farm_svc.fuelstations:
            slots_data = []
            for slot_id, slot in fs.slots.items():
                accepted_list = []
                for pid, pts in slot.accepted_products.items():
                    p_obj = stock_svc.get_product(pid) if stock_svc else None
                    p_name = p_obj.name if p_obj else f"PID {pid}"
                    p_stock = p_obj.total_amount if p_obj else 0
                    accepted_list.append({
                        "pid": pid,
                        "name": p_name,
                        "points": pts,
                        "stock": p_stock,
                    })

                slots_data.append({
                    "slotId": slot_id,
                    "level": slot.level,
                    "productionLimit": slot.production_limit,
                    "currentPoints": slot.current_points,
                    "pointsNeeded": slot.points_needed,
                    "levelPointsLeft": slot.level_points_left,
                    "busy": slot.busy,
                    "remain": slot.remain,
                    "isBlocked": slot.is_blocked,
                    "isFinished": slot.is_finished,
                    "isWaitingForRefill": slot.is_waiting_for_refill,
                    "acceptedProducts": accepted_list,
                })

            fs_states.append({
                "farmId": fs.farm_id,
                "position": fs.position,
                "level": fs.level,
                "tokens": fs.tokens,
                "slots": slots_data,
            })

    return {
        "config": settings.fuelstation.model_dump(),
        "fuelstations": fs_states,
    }


@fuelstation_router.put("")
async def update_fuelstation_config(
    payload: dict[str, Any] = Body(...),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Update fuelstation automation settings."""
    if "enabled" in payload:
        settings.fuelstation.enabled = bool(payload["enabled"])
    if "auto_harvest" in payload or "autoHarvest" in payload:
        settings.fuelstation.auto_harvest = bool(payload.get("auto_harvest", payload.get("autoHarvest")))
    if "auto_refill" in payload or "autoRefill" in payload:
        settings.fuelstation.auto_refill = bool(payload.get("auto_refill", payload.get("autoRefill")))
    if "min_reserve" in payload or "minReserve" in payload:
        try:
            settings.fuelstation.min_reserve = int(payload.get("min_reserve", payload.get("minReserve")))
        except (ValueError, TypeError):
            pass
    if "preferred_pids" in payload or "preferredPids" in payload:
        raw_pids = payload.get("preferred_pids", payload.get("preferredPids"))
        if isinstance(raw_pids, list):
            settings.fuelstation.preferred_pids = [int(p) for p in raw_pids if str(p).isdigit()]
    if "slot_preferred_pids" in payload or "slotPreferredPids" in payload:
        raw_slot_pids = payload.get("slot_preferred_pids", payload.get("slotPreferredPids"))
        if isinstance(raw_slot_pids, dict):
            new_slot_pids = {}
            for k, v in raw_slot_pids.items():
                if str(k).isdigit() and isinstance(v, list):
                    new_slot_pids[int(k)] = [int(p) for p in v if str(p).isdigit()]
            settings.fuelstation.slot_preferred_pids = new_slot_pids

    settings_manager.save()
    logger.info(f"API: Biosprit-Anlagen-Konfiguration aktualisiert: {settings.fuelstation}")
    return {"status": "ok", "config": settings.fuelstation.model_dump()}
