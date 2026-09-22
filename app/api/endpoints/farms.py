from typing import Any

from fastapi import APIRouter, Body, Depends
from loguru import logger

from app.config import settings
from app.core.auth import get_current_user
from app.core.settings_manager import settings_manager
from app.worker.scheduler import worker_scheduler

farms_router = APIRouter(prefix="/farms", tags=["Farms & Fields"])


@farms_router.get("", response_model=list[dict[str, Any]])
async def get_farms(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve farm overview with field status, crops, and configured target plants."""
    farm_svc = worker_scheduler.last_farm_service
    stock_svc = worker_scheduler.last_stock_service

    fields_by_farm: dict[int, list[dict[str, Any]]] = {}
    if farm_svc:
        for f in farm_svc.fields:
            first_tile = f.tiles[0] if f.tiles else None
            p_obj = stock_svc.get_product(first_tile.pid) if (stock_svc and first_tile) else None
            crop_name = p_obj.name if p_obj else (str(first_tile.pid) if first_tile else "Leer")

            fields_by_farm.setdefault(f.farm_id, []).append(
                {
                    "position": f.position,
                    "name": f.name,
                    "tilesCount": len(f.tiles),
                    "readyCount": f.ready_crops_count,
                    "cropPid": first_tile.pid if first_tile else None,
                    "cropName": crop_name,
                    "phase": first_tile.phase if first_tile else 0,
                    "remainSeconds": first_tile.remain_seconds if first_tile else 0,
                    "isWatered": first_tile.is_watered if first_tile else False,
                }
            )

    # Combine configured farms, specialized categories, and discovered fields
    all_farm_ids = sorted(
        set(
            list(settings.agriculture.farm_crops.keys())
            + list(settings.agriculture.farm_categories.keys())
            + list(fields_by_farm.keys())
            + [1, 2, 3, 4]
        )
    )
    result = []
    for f_id in all_farm_ids:
        target_pid = settings.agriculture.farm_crops.get(f_id)
        farm_cat = settings.agriculture.get_farm_category(f_id)
        target_plant = None
        if target_pid and stock_svc:
            p = stock_svc.get_product(target_pid)
            if p:
                target_plant = {
                    "pid": p.pid,
                    "name": p.name,
                    "price": p.price,
                    "category": p.category,
                }

        result.append(
            {
                "farmId": f_id,
                "category": farm_cat,
                "pid": target_pid,
                "plant": target_plant,
                "fields": fields_by_farm.get(f_id, []),
            }
        )

    return result


@farms_router.put("")
async def update_farms(
    payload: Any = Body(...),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Update configured target crops per farm (in-memory) with category validation."""
    updated_crops: dict[int, int] = {}
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and "farmId" in item and "pid" in item:
                try:
                    updated_crops[int(item["farmId"])] = int(item["pid"])
                except (ValueError, TypeError):
                    pass
    elif isinstance(payload, dict):
        for k, v in payload.items():
            try:
                updated_crops[int(k)] = int(v)
            except (ValueError, TypeError):
                pass

    # Validate crop category per farm (e.g. Farm 6: alpin, Farm 8: water, Farm 5: ex, Farm 10: spice)
    stock_svc = worker_scheduler.last_stock_service
    validated_crops: dict[int, int] = {}
    for f_id, pid in updated_crops.items():
        req_cat = settings.agriculture.get_farm_category(f_id)
        if stock_svc:
            p = stock_svc.get_product(pid)
            if p and p.category != req_cat:
                logger.warning(
                    f"API: Kann PID {pid} ('{p.name}') nicht für Farm {f_id} setzen: "
                    f"Farm erfordert strikt Kategorie '{req_cat}' (Produkt ist '{p.category}')."
                )
                continue
        validated_crops[f_id] = pid

    settings.agriculture.farm_crops = validated_crops
    settings_manager.save()
    logger.info(f"API: Farm-Anbauvorgaben aktualisiert: {settings.agriculture.farm_crops}")
    return {"status": "ok", "farm_crops": settings.agriculture.farm_crops}
