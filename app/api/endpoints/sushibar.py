from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from loguru import logger

from app.config import settings
from app.core.auth import get_current_user
from app.core.settings_manager import settings_manager
from app.worker.scheduler import worker_scheduler

sushibar_router = APIRouter(prefix="/sushibar", tags=["Sushi-Bar"])


@sushibar_router.get("", response_model=dict[str, Any])
async def get_sushibar_status(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve current Sushi-Bar status, slots, farmis, quest targets, and configuration."""
    sb_svc = worker_scheduler.last_sushibar_service
    summary = sb_svc.get_summary().model_dump() if sb_svc else None

    # Available recipe catalog for UI selection
    recipes_list = []
    if sb_svc and sb_svc.recipes:
        for pid, r in sorted(sb_svc.recipes.items(), key=lambda x: x[1].level):
            recipes_list.append({
                "pid": r.pid,
                "name": r.name,
                "level": r.level,
                "category": r.category,
                "costMoney": r.cost_money,
                "costCoins": r.cost_coins,
                "isCoinRecipe": r.is_coin_recipe,
                "duration": r.duration,
                "amount": r.amount,
                "needs": r.needs,
            })

    return {
        "config": settings.sushibar.model_dump(),
        "summary": summary,
        "recipes": recipes_list,
    }


@sushibar_router.get("/settings", response_model=dict[str, Any])
async def get_sushibar_settings(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve current Sushi-Bar automation settings."""
    return settings.sushibar.model_dump()


@sushibar_router.put("/settings", response_model=dict[str, Any])
async def update_sushibar_settings(
    payload: dict[str, Any] = Body(...),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Update Sushi-Bar automation settings."""
    cfg = settings.sushibar
    if "enabled" in payload:
        cfg.enabled = bool(payload["enabled"])
    if "auto_harvest" in payload:
        cfg.auto_harvest = bool(payload["auto_harvest"])
    if "auto_produce" in payload:
        cfg.auto_produce = bool(payload["auto_produce"])
    if "auto_train" in payload:
        cfg.auto_train = bool(payload["auto_train"])
    if "auto_farmi" in payload:
        cfg.auto_farmi = bool(payload["auto_farmi"])
    if "production_strategy" in payload:
        strat = str(payload["production_strategy"])
        if strat in ("quest5", "balanced", "preferred"):
            cfg.production_strategy = strat
    if "preferred_pids" in payload and isinstance(payload["preferred_pids"], list):
        cfg.preferred_pids = [int(p) for p in payload["preferred_pids"] if str(p).isdigit()]
    if "reserve_full_field" in payload:
        cfg.reserve_full_field = bool(payload["reserve_full_field"])
    if "coin_protection" in payload:
        cfg.coin_protection = bool(payload["coin_protection"])

    # Propagate to active service instance
    if worker_scheduler.last_sushibar_service:
        worker_scheduler.last_sushibar_service.config = cfg

    settings_manager.save()
    logger.info(f"Sushi-Bar: Konfiguration aktualisiert: {cfg.model_dump()}")
    return {
        "status": "success",
        "message": "Sushi-Bar-Einstellungen erfolgreich gespeichert.",
        "config": cfg.model_dump(),
    }


@sushibar_router.post("/action/harvest", response_model=dict[str, Any])
async def action_harvest(_: dict[str, Any] = Depends(get_current_user)):
    """Trigger manual harvest of completed kitchen slots."""
    sb_svc = worker_scheduler.last_sushibar_service
    if not sb_svc:
        raise HTTPException(
            status_code=400,
            detail="Sushi-Bar-Service ist aktuell nicht initialisiert. Bitte erst einen Zyklus ausführen.",
        )

    harvested = await sb_svc.kitchen.harvest()
    return {
        "status": "success",
        "harvested": harvested,
    }


@sushibar_router.post("/action/serve", response_model=dict[str, Any])
async def action_serve(_: dict[str, Any] = Depends(get_current_user)):
    """Trigger full serving cycle of the Sushi-Bar."""
    sb_svc = worker_scheduler.last_sushibar_service
    stock_svc = worker_scheduler.last_stock_service
    if not sb_svc or not stock_svc:
        raise HTTPException(
            status_code=400,
            detail="Sushi-Bar oder Lagerbestand nicht initialisiert.",
        )

    await sb_svc.serve(
        stock_service=stock_svc,
        quest_status_main=worker_scheduler.last_quest_requirements,
        catalog=stock_svc.catalog if stock_svc else None,
    )
    return {
        "status": "success",
        "summary": sb_svc.get_summary().model_dump(),
    }
