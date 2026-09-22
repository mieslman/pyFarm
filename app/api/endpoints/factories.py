from typing import Any

from fastapi import APIRouter, Depends
from loguru import logger

from app.config import FactoryConfig, settings
from app.core.auth import get_current_user
from app.core.settings_manager import settings_manager
from app.models.factory import FactoryBuildingData
from app.worker.scheduler import worker_scheduler

factories_router = APIRouter(prefix="/factories", tags=["Processing Factories"])


@factories_router.get("", response_model=list[FactoryBuildingData])
async def get_factories(
    _: dict[str, Any] = Depends(get_current_user),
) -> list[FactoryBuildingData]:
    """Retrieve runtime state and slot configurations of all discovered processing factories."""
    farm_svc = worker_scheduler.last_farm_service
    if not farm_svc or not farm_svc.factories:
        return []

    return [factory.to_building_data() for factory in farm_svc.factories]


@factories_router.get("/settings", response_model=FactoryConfig)
async def get_factory_settings(
    _: dict[str, Any] = Depends(get_current_user),
) -> FactoryConfig:
    """Get current factory automation settings."""
    return settings.factories


@factories_router.put("/settings", response_model=FactoryConfig)
async def update_factory_settings(
    new_settings: FactoryConfig,
    _: dict[str, Any] = Depends(get_current_user),
) -> FactoryConfig:
    """Update factory automation settings."""
    settings.factories = new_settings
    settings_manager.save()
    logger.info(f"Factory-Einstellungen aktualisiert: {settings.factories.model_dump()}")
    return settings.factories


@factories_router.post("/action/serve")
async def serve_factories(
    _: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Trigger a manual service cycle across all processing factories."""
    farm_svc = worker_scheduler.last_farm_service
    stock_svc = worker_scheduler.last_stock_service

    if not farm_svc or not stock_svc:
        return {"status": "error", "message": "FarmService oder StockService noch nicht bereit."}

    results = []
    for factory in farm_svc.factories:
        try:
            await factory.serve(
                stock_service=stock_svc,
                quest_status_main=worker_scheduler.last_quest_service.main_quests
                if worker_scheduler.last_quest_service
                else None,
                quest_requirements=worker_scheduler.last_quest_service.get_requirements_dict()
                if worker_scheduler.last_quest_service
                else None,
                catalog=worker_scheduler.last_catalog,
            )
            results.append(
                {
                    "farm": factory.farm_id,
                    "position": factory.position,
                    "name": factory.name,
                    "status": "success",
                }
            )
        except Exception as e:  # noqa: BLE001
            results.append(
                {
                    "farm": factory.farm_id,
                    "position": factory.position,
                    "name": factory.name,
                    "status": "error",
                    "error": str(e),
                }
            )

    return {"status": "completed", "factories": results}
