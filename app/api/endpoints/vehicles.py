from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from loguru import logger

from app.config import settings
from app.core.auth import get_current_user
from app.core.settings_manager import settings_manager
from app.models.vehicle import VehicleInfo, VehicleRouteConfig
from app.worker.scheduler import worker_scheduler

vehicles_router = APIRouter(prefix="/vehicles", tags=["Vehicles & Logistics"])


@vehicles_router.get("", response_model=list[VehicleInfo])
async def get_vehicles(_: dict[str, Any] = Depends(get_current_user)):
    """Retrieve all vehicles, their route assignments, locations, cargo, and status."""
    v_svc = worker_scheduler.last_vehicle_service
    if v_svc and v_svc.vehicles:
        return v_svc.get_vehicle_infos()

    # Fallback if bot hasn't completed first cycle yet
    infos = []
    for farm_id, r_cfg in settings.vehicles.routes.items():
        infos.append(
            VehicleInfo(
                route_id=r_cfg.route,
                vehicle_id=r_cfg.vehicle or 0,
                target_farm_id=farm_id,
                name="Fahrzeug (Initialisiert nach 1. Zyklus)",
                capacity=500,
                products_slots=2,
                duration=0,
                current_location=1,
                remain_seconds=0,
                status="ready_main",
                transport_enabled=r_cfg.transport,
                required_products=r_cfg.required_products,
                cargo=[],
                last_sent_cart="",
            )
        )
    return infos


@vehicles_router.post("/{route}/send", response_model=dict[str, Any])
async def send_vehicle_manual(
    route: int,
    cart: str = Body(default="", embed=True),
    _: dict[str, Any] = Depends(get_current_user),
):
    """Manually dispatch vehicle on specified route."""
    v_svc = worker_scheduler.last_vehicle_service
    if not v_svc or route not in v_svc.vehicles:
        raise HTTPException(
            status_code=404,
            detail=f"Fahrzeug auf Route {route} nicht gefunden oder Bot noch nicht initialisiert.",
        )

    success = await v_svc.send_manual(route_id=route, cart=cart)
    if not success:
        raise HTTPException(status_code=400, detail=f"Senden von Fahrzeug auf Route {route} fehlgeschlagen.")

    return {"status": "ok", "message": f"Fahrzeug auf Route {route} erfolgreich losgeschickt."}


@vehicles_router.put("/{farm_id}/config", response_model=dict[str, Any])
async def update_vehicle_config(
    farm_id: int,
    config_update: dict[str, Any],
    _: dict[str, Any] = Depends(get_current_user),
):
    """Update route settings for a specific farm."""
    if farm_id not in settings.vehicles.routes:
        settings.vehicles.routes[farm_id] = VehicleRouteConfig(
            farm_id=farm_id,
            route=config_update.get("route", 1),
            vehicle=config_update.get("vehicle"),
            transport=config_update.get("transport", True),
        )

    route_cfg = settings.vehicles.routes[farm_id]
    if "transport" in config_update:
        route_cfg.transport = bool(config_update["transport"])
    if "auto_fastest" in config_update:
        route_cfg.auto_fastest = bool(config_update["auto_fastest"])
    if "send_partial" in config_update:
        route_cfg.send_partial = bool(config_update["send_partial"])
    if "prioritize_quests" in config_update:
        route_cfg.prioritize_quests = bool(config_update["prioritize_quests"])
    if "only_milled_surplus" in config_update:
        route_cfg.only_milled_surplus = bool(config_update["only_milled_surplus"])
    if "only_quest_products" in config_update:
        route_cfg.only_quest_products = bool(config_update["only_quest_products"])
    if "sushi_supply" in config_update:
        route_cfg.sushi_supply = bool(config_update["sushi_supply"])
    if "sushi_reserve_threshold" in config_update:
        route_cfg.sushi_reserve_threshold = int(config_update["sushi_reserve_threshold"])
    if "min_crop_reserve" in config_update:
        route_cfg.min_crop_reserve = int(config_update["min_crop_reserve"])
    if "required_products" in config_update and isinstance(config_update["required_products"], list):
        route_cfg.required_products = config_update["required_products"]

    settings_manager.save()
    logger.info(f"Fahrzeug-Konfiguration für Farm {farm_id} aktualisiert: {route_cfg.model_dump()}")
    return {"status": "ok", "route_config": route_cfg.model_dump()}
