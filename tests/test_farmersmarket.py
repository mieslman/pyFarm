"""Unit and integration tests for FarmersMarket (Bauernmarkt Dorf 2)."""

from typing import Any

import httpx
import pytest
import respx

from app.config import FarmersMarketConfig, settings
from app.core.client import MFFGameClient
from app.models.product import Product
from app.modules.farmersmarket.farmis import MarketFarmisService
from app.modules.farmersmarket.flower_area import FlowerAreaService
from app.modules.farmersmarket.flower_slots import FlowerSlotsService
from app.modules.farmersmarket.nursery import NurseryService
from app.modules.farmersmarket.order_manager import FlowerOrderManager
from app.modules.farmersmarket.petbreed import PetBreedService
from app.modules.farmersmarket.service import FarmersMarketService


class DummyStockService:
    """Mock stock service providing predefined inventory amounts and products."""

    def __init__(self, inventory: dict[int, int] | None = None) -> None:
        self.inventory: dict[int, int] = inventory or {}
        self.products: dict[int, Product] = {}
        for pid, amt in self.inventory.items():
            cat = "fl" if 170 <= pid <= 199 else ("fla" if 200 <= pid <= 230 else "v")
            self.products[pid] = Product(pid=pid, name=f"Product {pid}", category=cat, amount=amt, price=10.0)

    def get_amount(self, pid: int) -> int:
        return self.inventory.get(pid, 0)

    async def grasp_products(self, requirements: list[dict[str, int]]) -> bool:
        return True

    def update(self, updateblock: dict[str, Any]) -> None:
        pass


@pytest.mark.asyncio
async def test_nursery_harvest_and_produce(fast_client: MFFGameClient):
    """Test Gärtnerei (Nursery) harvesting ready arrangements and crafting demanded ones."""
    fast_client.rid = "test_rid"
    stock = DummyStockService({171: 50, 172: 50, 173: 50})
    nursery = NurseryService(fast_client, stock)  # type: ignore[arg-type]

    raw_data = {
        "nursery": {
            "slots": {
                "1": {"slot": "1", "pid": "200", "remain": 0, "duration": 1000},  # Ready
                "2": {"slot": "2", "pid": None, "remain": 0, "block": 0, "coins": 0},  # Free
                "3": {"slot": "3", "block": 1, "coins": 5},  # Blocked / Coins
            },
            "products": {
                "200": {
                    "farmipoints": 7,
                    "products": {"171": 10, "172": 10},
                    "duration": 14400,
                    "coins": 0,
                },
                "201": {
                    "farmipoints": 10,
                    "products": {"171": 100},  # Not enough
                    "duration": 14400,
                    "coins": 0,
                },
            },
        }
    }
    nursery.update(raw_data)

    order_manager = FlowerOrderManager()
    order_manager.add_order(200, 1)

    with respx.mock:
        # Mock harvest call
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            side_effect=[
                httpx.Response(200, json={"datablock": [1, []]}),  # nursery_harvest
                httpx.Response(200, json={"datablock": 1}),  # nursery_startproduction
            ]
        )

        harvested = await nursery.harvest()
        assert harvested == 1
        assert nursery.state.slots[1].pid is None

        produced = await nursery.produce(order_manager)
        assert produced == 1
        assert order_manager.get_demand(200) == 0


@pytest.mark.asyncio
async def test_flower_area_harvest_water_plant(fast_client: MFFGameClient):
    """Test Blumenwiese (FlowerArea) harvesting, watering, and planting flowers."""
    fast_client.rid = "test_rid"
    # 50x PID 171, 20x PID 172 (both category 'fl') -> PID 172 has smallest quantity
    stock = DummyStockService({171: 50, 172: 20})
    flower_area = FlowerAreaService(fast_client, stock)  # type: ignore[arg-type]

    # Case 1: Partial ready - some fields remain >= 0 -> harvest should NOT trigger
    partial_data = {
        "flower_area": {
            "1": {"pid": "171", "remain": -100, "water_remain": 100},  # Ready (< 0)
            "2": {"pid": "171", "remain": 500, "water_remain": 100},   # Growing (>= 0)
        }
    }
    flower_area.update(partial_data)
    assert await flower_area.harvest() == 0

    # Case 2: All planted fields ready (remain < 0) -> harvest_all triggers
    ready_data = {
        "flower_area": {
            "1": {"pid": "171", "remain": -200, "water_remain": 100},
            "2": {"pid": "171", "remain": -50, "water_remain": 100},
        }
    }
    flower_area.update(ready_data)

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            side_effect=[
                httpx.Response(200, json={"updateblock": {"farmersmarket": {"flower_area": []}}}),  # harvest_all
                httpx.Response(200, json={"updateblock": {"farmersmarket": {"flower_area": {str(i): {"pid": "172", "remain": 14000, "water_remain": -10} for i in range(1, 37)}}}}),  # autoplant
                httpx.Response(200, json={"updateblock": {"farmersmarket": {"flower_area": {str(i): {"pid": "172", "remain": 14000, "water_remain": 86400} for i in range(1, 37)}}}}),  # water_all
            ]
        )

        harvested = await flower_area.harvest()
        assert harvested == 2
        assert all(f.is_empty for f in flower_area.state.fields.values())

        # 2. Autoplant: chooses PID 172 (smallest quantity 20 vs 50)
        planted = await flower_area.plant()
        assert planted == 36
        assert flower_area.state.fields[1].pid == 172

        # 3. Water all: water_remain is -10 (< 0)
        watered = await flower_area.water()
        assert watered is True
        assert flower_area.state.fields[1].water_remain == 86400

        # Subsequent water call does nothing because water_remain >= 0
        assert await flower_area.water() is False


@pytest.mark.asyncio
async def test_flower_slots_remove_water_plant(fast_client: MFFGameClient):
    """Test FlowerSlots maintenance: remove expired, water, plant arrangement."""
    fast_client.rid = "test_rid"
    stock = DummyStockService({200: 5})  # 5x Gesteck PID 200
    slots_service = FlowerSlotsService(fast_client, stock)  # type: ignore[arg-type]

    raw_data = {
        "flower_slots": {
            "slots": {
                "1": {"pid": "201", "remain": -10, "waterremain": 0, "points": 80},  # Expired
            },
            "points": "1000",
        }
    }
    slots_service.update(raw_data)

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            side_effect=[
                httpx.Response(200, json={"datablock": 1}),  # remove
                httpx.Response(200, json={"datablock": 1}),  # plant
            ]
        )

        removed = await slots_service.remove_expired()
        assert removed == 1
        assert slots_service.state.slots[1].is_empty is True

        planted = await slots_service.plant_arrangement()
        assert planted is True
        assert slots_service.state.slots[1].pid == 200


@pytest.mark.asyncio
async def test_farmis_demand_and_serve(fast_client: MFFGameClient):
    """Test MarketFarmis serving eligible customers and registering demand for missing arrangements."""
    fast_client.rid = "test_rid"
    stock = DummyStockService({171: 50, 200: 0})  # Has 171, missing 200
    farmis_service = MarketFarmisService(fast_client, stock)  # type: ignore[arg-type]
    order_manager = FlowerOrderManager()

    raw_data = {
        "farmis": [
            {
                "id": "101",
                "price": "500",
                "points": "1000",
                "cart": [{"pid": 200, "amount": 2}],  # Demands missing arrangement
                "status": "0",
            },
            {
                "id": "102",
                "price": "800",
                "points": "1500",
                "cart": [{"pid": 171, "amount": 10}],  # All available!
                "status": "0",
            },
        ]
    }
    farmis_service.update(raw_data)

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": 1})
        )

        served = await farmis_service.serve_and_collect_orders(order_manager)
        assert served == 1  # Farmi 102 served
        assert order_manager.get_demand(200) == 2  # Farmi 101 demand registered


@pytest.mark.asyncio
async def test_petbreed_inactive_by_default(fast_client: MFFGameClient):
    """Verify that PetBreedService strictly makes 0 API calls when inactive (default config)."""
    fast_client.rid = "test_rid"
    pet_service = PetBreedService(fast_client, enabled=False)

    raw_data = {
        "pets": {
            "production": {
                "1": {"duration": 100, "gone": 100, "1": {"pid": "50"}},  # Ready
            }
        }
    }
    pet_service.update(raw_data)

    with respx.mock:
        # If any request is made, respx will fail or record it
        route = respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": 1})
        )

        res = await pet_service.run_cycle()
        assert res == 0
        assert route.call_count == 0  # 0 calls made!

    # Verify that when enabled, it DOES harvest
    pet_service.enabled = True
    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": 1})
        )

        res = await pet_service.run_cycle()
        assert res == 1


@pytest.mark.asyncio
async def test_farmersmarket_service_full_cycle(fast_client: MFFGameClient):
    """Test full cycle of FarmersMarketService orchestrator."""
    fast_client.rid = "test_rid"
    stock = DummyStockService({171: 50, 172: 50, 200: 5})
    cfg = FarmersMarketConfig(
        enabled=True,
        nursery_enabled=True,
        flower_area_enabled=True,
        flower_slots_enabled=True,
        farmis_enabled=True,
        pet_breed_enabled=False,  # Inactive
    )
    service = FarmersMarketService(fast_client, stock, cfg)  # type: ignore[arg-type]

    raw_getfarms = {
        "datablock": [1, []],
        "updateblock": {
            "farmersmarket": {
                "nursery": {
                    "slots": {"1": {"slot": "1", "pid": None, "remain": 0, "block": 0, "coins": 0}},
                    "products": {},
                },
                "flower_area": {
                    "1": {"pid": "171", "remain": 5000, "water_remain": 10000},
                },
                "flower_slots": {"slots": {}},
                "farmis": [],
                "pets": {},
            }
        },
    }

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json=raw_getfarms)
        )

        res = await service.run_cycle()
        assert "farmis_served" in res
        assert "nursery_harvested" in res

        summary = service.get_summary()
        assert summary.enabled is True
        assert summary.pet_breed_enabled is False
        assert summary.pet_breed_status == "Inaktiv (Konfiguration)"
        assert summary.flower_fields_total == 36


@pytest.mark.asyncio
async def test_farmersmarket_api_endpoints():
    """Test FastAPI REST endpoints for FarmersMarket."""
    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Login to obtain JWT
        login_res = await ac.post(
            "/api/v1/login",
            json={"username": settings.api_username, "password": settings.api_password},
        )
        assert login_res.status_code == 200
        token = login_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2. GET /farmersmarket
        fm_res = await ac.get("/api/v1/farmersmarket", headers=headers)
        assert fm_res.status_code == 200
        data = fm_res.json()
        assert "pet_breed_enabled" in data
        assert data["pet_breed_enabled"] is False

        # 3. GET /farmersmarket/settings
        cfg_res = await ac.get("/api/v1/farmersmarket/settings", headers=headers)
        assert cfg_res.status_code == 200
        assert cfg_res.json()["pet_breed_enabled"] is False

        # 4. PUT /farmersmarket/settings
        put_res = await ac.put(
            "/api/v1/farmersmarket/settings",
            json={"max_flower_batch": 8},
            headers=headers,
        )
        assert put_res.status_code == 200
        assert put_res.json()["farmersmarket_config"]["max_flower_batch"] == 8
