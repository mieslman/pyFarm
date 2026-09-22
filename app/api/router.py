from fastapi import APIRouter

from app.api.endpoints.auth import auth_router
from app.api.endpoints.bot import bot_router
from app.api.endpoints.config import config_router
from app.api.endpoints.contracts import contracts_router
from app.api.endpoints.events import events_router
from app.api.endpoints.factories import factories_router
from app.api.endpoints.farmersmarket import farmersmarket_router
from app.api.endpoints.farms import farms_router
from app.api.endpoints.foodworld import foodworld_router
from app.api.endpoints.forestry import forestry_router
from app.api.endpoints.fuelstation import fuelstation_router
from app.api.endpoints.insecthotel import insecthotel_router
from app.api.endpoints.offers import offers_router
from app.api.endpoints.plants import plants_router
from app.api.endpoints.seasonpass import seasonpass_router
from app.api.endpoints.spicehouse import spicehouse_router
from app.api.endpoints.stall import stall_router
from app.api.endpoints.stock import stock_router
from app.api.endpoints.sushibar import sushibar_router
from app.api.endpoints.vehicles import vehicles_router

api_router = APIRouter()


@api_router.get("/health", tags=["Health"])
async def health_check():
    """Basic health check endpoint."""
    return {"status": "ok"}


# Include domain sub-routers
api_router.include_router(auth_router)
api_router.include_router(plants_router)
api_router.include_router(farms_router)
api_router.include_router(stock_router)
api_router.include_router(offers_router)
api_router.include_router(contracts_router)
api_router.include_router(forestry_router)
api_router.include_router(farmersmarket_router)
api_router.include_router(foodworld_router)
api_router.include_router(fuelstation_router)
api_router.include_router(sushibar_router)
api_router.include_router(spicehouse_router)
api_router.include_router(seasonpass_router)
api_router.include_router(events_router)
api_router.include_router(insecthotel_router)
api_router.include_router(stall_router)
api_router.include_router(factories_router)
api_router.include_router(vehicles_router)
api_router.include_router(bot_router)
api_router.include_router(config_router)


