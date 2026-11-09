from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from loguru import logger

from app.config import settings
from app.core.auth import get_current_user
from app.core.settings_manager import settings_manager
from app.worker.scheduler import worker_scheduler

crafting_village_router = APIRouter(prefix="/crafting", tags=["Handwerkerdorf"])


@crafting_village_router.get("", response_model=dict[str, Any])
async def get_crafting_village_status(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve current Handwerkerdorf status (quarry/mine, workers, tools, productions)."""
    cv_svc = worker_scheduler.last_crafting_village_service
    summary = (
        cv_svc.get_summary().model_dump()
        if cv_svc
        else {
            "active": False,
            "mine_level": 1,
            "workers_count": 0,
            "working_workers_count": 0,
            "active_productions_count": 0,
            "ready_productions_count": 0,
            "sandstone_stock": 0,
            "granite_stock": 0,
            "last_updated": None,
        }
    )

    snapshot_data = cv_svc.snapshot.model_dump() if (cv_svc and cv_svc.snapshot) else None

    return {
        "config": settings.crafting_village.model_dump(),
        "summary": summary,
        "snapshot": snapshot_data,
    }


@crafting_village_router.get("/settings", response_model=dict[str, Any])
async def get_crafting_village_settings(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve current Handwerkerdorf automation settings."""
    return settings.crafting_village.model_dump()


@crafting_village_router.put("/settings", response_model=dict[str, Any])
async def update_crafting_village_settings(
    payload: dict[str, Any] = Body(...),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Update Handwerkerdorf automation settings."""
    cfg = settings.crafting_village
    if "enabled" in payload:
        cfg.enabled = bool(payload["enabled"])
    if "auto_harvest" in payload:
        cfg.auto_harvest = bool(payload["auto_harvest"])
    if "auto_mine" in payload:
        cfg.auto_mine = bool(payload["auto_mine"])
    if "auto_refill_energy" in payload:
        cfg.auto_refill_energy = bool(payload["auto_refill_energy"])
    if "auto_buy_tools" in payload:
        cfg.auto_buy_tools = bool(payload["auto_buy_tools"])
    if "coin_protection" in payload:
        cfg.coin_protection = bool(payload["coin_protection"])
    if "preferred_material" in payload:
        mat = str(payload["preferred_material"]).strip().lower()
        if mat in ("least_stock", "sandstone", "granite"):
            cfg.preferred_material = mat
    if "auto_stonemason" in payload:
        cfg.auto_stonemason = bool(payload["auto_stonemason"])
    if "auto_buy_stonemason_tools" in payload:
        cfg.auto_buy_stonemason_tools = bool(payload["auto_buy_stonemason_tools"])
    if "stonemason_reward_priority" in payload:
        prio = str(payload["stonemason_reward_priority"]).strip().lower()
        if prio in ("overall", "stonemason_points", "money", "points"):
            cfg.stonemason_reward_priority = prio

    settings_manager.save_settings()
    logger.info(f"Handwerkerdorf-Einstellungen aktualisiert: {cfg.model_dump()}")
    return {"status": "ok", "settings": cfg.model_dump()}


@crafting_village_router.post("/action/refill", response_model=dict[str, Any])
async def refill_worker_action(
    payload: dict[str, Any] = Body(default_factory=dict),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Refill energy of a specific worker or all workers with <100 energy."""
    cv_svc = worker_scheduler.last_crafting_village_service
    if not cv_svc or not cv_svc.client:
        raise HTTPException(
            status_code=503,
            detail="Game-Client oder Handwerkerdorf-Service nicht verfügbar.",
        )

    worker_id = payload.get("worker_id")
    pid = payload.get("pid")
    amount = payload.get("amount")
    stock_service = worker_scheduler.last_stock_service

    if worker_id:
        success = await cv_svc.refill_worker_energy(
            worker_id=worker_id, pid=pid, amount=amount, stock_service=stock_service
        )
        return {"status": "ok" if success else "failed", "worker_id": str(worker_id)}

    # Refill all workers with energy < 100
    if not cv_svc.snapshot or not cv_svc.snapshot.mine:
        await cv_svc.init_remote()

    refilled = 0
    if cv_svc.snapshot and cv_svc.snapshot.mine:
        for wid, w in cv_svc.snapshot.mine.workers.items():
            if w.energy < 100:
                ok = await cv_svc.refill_worker_energy(
                    worker_id=wid, pid=pid, amount=amount, stock_service=stock_service
                )
                if ok:
                    refilled += 1

    return {"status": "ok", "refilled_workers": refilled}


@crafting_village_router.post("/action/harvest", response_model=dict[str, Any])
async def harvest_action(
    payload: dict[str, Any] = Body(default_factory=dict),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Harvest completed mining production slots."""
    cv_svc = worker_scheduler.last_crafting_village_service
    if not cv_svc or not cv_svc.client:
        raise HTTPException(
            status_code=503,
            detail="Game-Client oder Handwerkerdorf-Service nicht verfügbar.",
        )

    slot = payload.get("slot", 1)
    position = payload.get("position", 1)
    reward = await cv_svc.finish_production(slot=slot, position=position)
    return {"status": "ok" if reward else "failed", "reward": reward}


@crafting_village_router.post("/action/buy_tool", response_model=dict[str, Any])
async def buy_tool_action(
    payload: dict[str, Any] = Body(default_factory=dict),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Buy equipment tool in quarry shop."""
    cv_svc = worker_scheduler.last_crafting_village_service
    if not cv_svc or not cv_svc.client:
        raise HTTPException(
            status_code=503,
            detail="Game-Client oder Handwerkerdorf-Service nicht verfügbar.",
        )

    name = payload.get("name", "shoes")
    amount = int(payload.get("amount", 1))
    stock_service = worker_scheduler.last_stock_service

    success = await cv_svc.buy_shop_item(name=name, amount=amount, stock_service=stock_service)
    return {"status": "ok" if success else "failed", "tool": name, "amount": amount}



@crafting_village_router.post("/action/stonemason_set_slot", response_model=dict[str, Any])
async def stonemason_set_slot_action(
    payload: dict[str, Any] = Body(...),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Assign a waiting customer to a stonemason workbench slot."""
    cv_svc = worker_scheduler.last_crafting_village_service
    if not cv_svc or not cv_svc.client:
        raise HTTPException(
            status_code=503,
            detail="Game-Client oder Handwerkerdorf-Service nicht verfügbar.",
        )

    customer_id = payload.get("customer_id")
    slot = int(payload.get("slot", 1))
    if not customer_id:
        raise HTTPException(status_code=400, detail="customer_id ist erforderlich.")

    stock_service = worker_scheduler.last_stock_service
    success = await cv_svc.assign_customer_to_slot(
        customer_id=customer_id, slot=slot, stock_service=stock_service
    )
    return {"status": "ok" if success else "failed", "customer_id": str(customer_id), "slot": slot}


@crafting_village_router.post("/action/stonemason_click_tile", response_model=dict[str, Any])
async def stonemason_click_tile_action(
    payload: dict[str, Any] = Body(...),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Start chiseling a tile on a stonemason workbench slot."""
    cv_svc = worker_scheduler.last_crafting_village_service
    if not cv_svc or not cv_svc.client:
        raise HTTPException(
            status_code=503,
            detail="Game-Client oder Handwerkerdorf-Service nicht verfügbar.",
        )

    customer_id = payload.get("customer_id")
    tile = int(payload.get("tile", 1))
    tool_id = int(payload.get("tool_id", 1))
    if not customer_id:
        raise HTTPException(status_code=400, detail="customer_id ist erforderlich.")

    stock_service = worker_scheduler.last_stock_service
    success = await cv_svc.chisel_tile(
        customer_id=customer_id, tile=tile, tool_id=tool_id, stock_service=stock_service
    )
    return {
        "status": "ok" if success else "failed",
        "customer_id": str(customer_id),
        "tile": tile,
        "tool_id": tool_id,
    }


@crafting_village_router.post("/action/stonemason_finish_grid", response_model=dict[str, Any])
async def stonemason_finish_grid_action(
    payload: dict[str, Any] = Body(...),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Finish a completed sculpture grid and collect rewards."""
    cv_svc = worker_scheduler.last_crafting_village_service
    if not cv_svc or not cv_svc.client:
        raise HTTPException(
            status_code=503,
            detail="Game-Client oder Handwerkerdorf-Service nicht verfügbar.",
        )

    slot = int(payload.get("slot", 1))
    stock_service = worker_scheduler.last_stock_service
    reward = await cv_svc.finish_grid(slot=slot, stock_service=stock_service)
    return {"status": "ok" if reward else "failed", "slot": slot, "reward": reward}


@crafting_village_router.post("/action/stonemason_buy_tool", response_model=dict[str, Any])
async def stonemason_buy_tool_action(
    payload: dict[str, Any] = Body(default_factory=dict),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Buy stonemason tools (Meißel) using kT."""
    cv_svc = worker_scheduler.last_crafting_village_service
    if not cv_svc or not cv_svc.client:
        raise HTTPException(
            status_code=503,
            detail="Game-Client oder Handwerkerdorf-Service nicht verfügbar.",
        )

    tool_id = int(payload.get("tool_id", 1))
    currency_amount = int(payload.get("currency_amount", 500))
    stock_service = worker_scheduler.last_stock_service
    success = await cv_svc.buy_stonemason_tool(
        tool_id=tool_id, currency_amount=currency_amount, stock_service=stock_service
    )
    return {
        "status": "ok" if success else "failed",
        "tool_id": tool_id,
        "currency_amount": currency_amount,
    }
