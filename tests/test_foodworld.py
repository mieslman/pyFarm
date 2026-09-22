"""Unit and integration tests for Foodworld (Phase 9 - Foodworld Gastronomie & Restaurant)."""

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import FoodworldConfig, settings
from app.core.client import MFFGameClient
from app.models.product import MarketOffer, Product
from app.modules.foodworld.kitchen import KitchenService
from app.modules.foodworld.models import (
    FoodworldBuilding,
    FoodworldFarmi,
    FoodworldRecipe,
    FoodworldSlot,
    TableChair,
    TableGroup,
)
from app.modules.foodworld.service import FoodworldService
from app.modules.foodworld.tables import TableService


class DummyStockService:
    """Mock stock service providing predefined inventory amounts and products."""

    def __init__(self, inventory: dict[int, int] | None = None) -> None:
        self.inventory: dict[int, int] = inventory or {}
        self.products: dict[int, Product] = {}
        for pid, amt in self.inventory.items():
            cat = "fw" if (130 <= pid <= 169 or 450 <= pid <= 485) else "v"
            self.products[pid] = Product(
                pid=pid,
                name=f"FoodProduct {pid}",
                category=cat,
                amount=amt,
                price=50.0,
            )

    def get_amount(self, pid: int) -> int:
        return self.inventory.get(pid, 0)

    async def grasp_products(self, requirements: list[dict[str, int]]) -> bool:
        return True

    def update(self, updateblock: dict[str, Any]) -> None:
        pass


@pytest.mark.asyncio
async def test_foodworld_models():
    """Test Pydantic models serialization and helpers for Foodworld."""
    slot_ready = FoodworldSlot(slot_id=1, pid=131, remain=0, duration=3600)
    assert slot_ready.is_ready is True
    assert slot_ready.is_free is False

    slot_blocked = FoodworldSlot(slot_id=2, pid=None, is_blocked=True, coins=5)
    assert slot_blocked.is_ready is False
    assert slot_blocked.is_free is False

    building = FoodworldBuilding(
        id=1, name="Pikante Pfanne", level=2, slots={1: slot_ready, 2: slot_blocked}
    )
    assert len(building.ready_slots) == 1
    assert len(building.free_slots) == 0

    chair_ready = TableChair(chair_id=1, farmi_id="999", remain=0)
    assert chair_ready.is_ready is True
    assert chair_ready.is_free is False

    chair_free = TableChair(chair_id=2, farmi_id=None, remain=0)
    assert chair_free.is_ready is False
    assert chair_free.is_free is True

    table = TableGroup(table_id=0, is_unlocked=True, chairs={1: chair_ready, 2: chair_free})
    assert len(table.ready_chairs) == 1
    assert len(table.free_chairs) == 1

    farmi = FoodworldFarmi(id="501", status=0, price=8500, cart={131: 2})
    assert farmi.price == 8500
    assert farmi.cart[131] == 2

    recipe = FoodworldRecipe(
        id=10, building_id=1, output_pid=131, output_amount=1, requirements={2: 5, 4: 2}
    )
    assert recipe.output_pid == 131


@pytest.mark.asyncio
async def test_kitchen_service_crop_and_cook(fast_client: MFFGameClient):
    """Test KitchenService collecting finished dishes and cooking new ones without spending coins."""
    fast_client.rid = "test_rid"
    # Ingredients: pid 2 and pid 4 available in stock
    stock = DummyStockService({2: 50, 4: 50})
    kitchen = KitchenService(fast_client, stock)  # type: ignore[arg-type]

    raw_datablock = {
        "buildings": {
            "1": {
                "name": "Snack-Bar",
                "level": 1,
                "slots": {
                    "1": {
                        "slot": "1",
                        "pid": "131",
                        "remain": 0,
                        "duration": 1800,
                        "ready": 1,
                    },  # Ready to crop
                    "2": {
                        "slot": "2",
                        "pid": None,
                        "remain": 0,
                        "block": 0,
                        "coins": 0,
                    },  # Free to cook
                    "3": {
                        "slot": "3",
                        "pid": None,
                        "remain": 0,
                        "block": 1,
                        "coins": 10,
                    },  # Blocked / Coins!
                },
            }
        },
        "products": {
            "10": {
                "pos": 1,
                "out": {"131": 1},
                "in": {"2": 5, "4": 2},
                "star": 0,
            }
        },
    }

    cropped_calls: list[dict] = []
    production_calls: list[dict] = []

    async def fake_api_call(endpoint: str, params: dict) -> dict:
        if params.get("action") == "crop":
            cropped_calls.append(params)
            return {"datablock": 1}
        if params.get("action") == "production":
            production_calls.append(params)
            return {"datablock": 1}
        return {"datablock": 1}

    fast_client.api_call = fake_api_call  # type: ignore[method-assign]

    # 1. Update from datablock
    kitchen.update(raw_datablock)
    assert len(kitchen.buildings) == 1
    assert 1 in kitchen.buildings
    assert len(kitchen.recipes) == 1

    # 2. Collect finished dishes
    cropped = await kitchen.pickup_products()
    assert cropped == 1
    assert len(cropped_calls) == 1
    assert cropped_calls[0]["action"] == "crop"
    assert cropped_calls[0]["table"] == 1
    assert cropped_calls[0]["chair"] == 1

    # 3. Cook dishes into free slots (only slot 2 should be used, NOT slot 3 which costs coins!)
    cooked = await kitchen.produce(reserve_buffer=50)
    assert cooked == 1
    assert len(production_calls) == 1
    assert production_calls[0]["action"] == "production"
    assert production_calls[0]["table"] == 1
    assert production_calls[0]["chair"] == 1
    assert production_calls[0]["id"] == 10  # Recipe ID


@pytest.mark.asyncio
async def test_table_service_cash_and_seat(fast_client: MFFGameClient):
    """Test TableService cashing out seated guests and seating waiting farmis by profitability."""
    fast_client.rid = "test_rid"
    # User has 10x Hawaii-Toast (PID 131) in stock for Farmi
    stock = DummyStockService({131: 10})
    tables = TableService(fast_client, stock)  # type: ignore[arg-type]

    raw_datablock = {
        "tables": {
            "0": {
                "block": 0,
                "locked": 0,
                "chairs": {
                    "1": {
                        "chair": "1",
                        "id": "9991",
                        "ready": 1,
                        "remain": 0,
                    },  # Finished eating -> cash out!
                    "2": {"chair": "2", "id": None, "remain": 0},  # Free chair -> seat farmi!
                },
            }
        },
        "farmis": [
            {
                "id": "1001",
                "status": 0,
                "price": 4500,
                "points": 100,
                "cart": {"131": 2},  # Farmi wants 2x PID 131, we have 10 in stock
            }
        ],
    }

    cash_calls: list[dict] = []
    seat_calls: list[dict] = []

    async def fake_api_call(endpoint: str, params: dict) -> dict:
        if params.get("action") == "cash":
            cash_calls.append(params)
            return {"datablock": {"transfer": {"money": 8942.50}}}
        if params.get("action") == "dropped":
            seat_calls.append(params)
            return {"datablock": 1}
        return {"datablock": 1}

    fast_client.api_call = fake_api_call  # type: ignore[method-assign]

    tables.update(raw_datablock)
    assert len(tables.tables) == 1
    assert len(tables.farmis) == 1

    # 1. Cash out guest at table 0, chair 1
    cashed, revenue = await tables.cash_tables()
    assert cashed == 1
    assert revenue == 8942.50
    assert len(cash_calls) == 1
    assert cash_calls[0]["table"] == 0
    assert cash_calls[0]["chair"] == 1

    # 2. Seat waiting farmi into table 0, chair 2
    seated = await tables.seat_guests()
    assert seated == 1
    assert len(seat_calls) == 1
    assert seat_calls[0]["action"] == "dropped"
    assert seat_calls[0]["table"] == 0
    assert seat_calls[0]["chair"] == 1
    assert seat_calls[0]["id"] == "1001"


@pytest.mark.asyncio
async def test_foodworld_service_reserve_buffer_and_empty_market(fast_client: MFFGameClient):
    """Test reserve buffer 50 and zero-market-offer rule for food export."""
    fast_client.rid = "test_rid"

    # Inventory:
    # PID 131 (Hawaii-Toast): 53 in stock -> surplus = 3 (< 5 minimum batch) -> no export
    # PID 132 (Pizza): 80 in stock -> surplus = 30 -> check market:
    #   Case A: market has offers -> skip!
    #   Case B: market has 0 offers -> export 30!
    # PID 133 (Salat): 40 in stock -> <= reserve (50) -> no export
    stock = DummyStockService({131: 53, 132: 80, 133: 40})

    market_mock = AsyncMock()

    config = FoodworldConfig(
        enabled=True,
        dish_reserve_buffer=50,
        only_empty_market=True,
        market_export_enabled=True,
    )

    created_offers: list[dict] = []

    async def fake_api_call(endpoint: str, params: dict) -> dict:
        if params.get("mode") == "marketcreateoffer":
            created_offers.append(params)
            return {"datablock": 1}
        return {"datablock": {}}

    fast_client.api_call = fake_api_call  # type: ignore[method-assign]

    service = FoodworldService(
        fast_client,
        stock_service=stock,  # type: ignore[arg-type]
        market_service=market_mock,
        config=config,
    )

    # Case A: Market already has offers for PID 132
    market_mock.get_offers.return_value = [MarketOffer(offer_id=1, pid=132, amount=10, price=55.0)]

    exported = await service._export_surplus_dishes()
    assert exported == 0
    assert len(created_offers) == 0

    # Case B: Market has 0 offers for PID 132 (Empty market!)
    market_mock.get_offers.return_value = []

    exported = await service._export_surplus_dishes()
    # 80 in stock - 50 reserve = 30 surplus exported!
    assert exported == 30
    assert len(created_offers) == 1
    assert created_offers[0]["pid"] == 132
    assert created_offers[0]["amount"] == 30


@pytest.mark.asyncio
async def test_foodworld_api_endpoints():
    """Test FastAPI REST endpoints for Foodworld (/api/v1/foodworld)."""
    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        # 1. Login to obtain JWT
        login_res = await ac.post(
            "/api/v1/login",
            json={"username": settings.api_username, "password": settings.api_password},
        )
        assert login_res.status_code == 200
        token = login_res.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2. GET /foodworld
        fw_res = await ac.get("/api/v1/foodworld", headers=headers)
        assert fw_res.status_code == 200
        data = fw_res.json()
        assert "enabled" in data
        assert "kitchen_slots_total" in data
        assert "dishes_exported_count" in data

        # 3. GET /foodworld/settings
        cfg_res = await ac.get("/api/v1/foodworld/settings", headers=headers)
        assert cfg_res.status_code == 200
        cfg_data = cfg_res.json()
        assert cfg_data["dish_reserve_buffer"] == 50
        assert cfg_data["only_empty_market"] is True
        assert cfg_data["auto_unlock_tables"] is False

        # 4. PUT /foodworld/settings
        put_res = await ac.put(
            "/api/v1/foodworld/settings",
            json={"dish_reserve_buffer": 55, "only_empty_market": True},
            headers=headers,
        )
        assert put_res.status_code == 200
        assert put_res.json()["settings"]["dish_reserve_buffer"] == 55

        # Reset buffer back to 50
        await ac.put(
            "/api/v1/foodworld/settings",
            json={"dish_reserve_buffer": 50},
            headers=headers,
        )


@pytest.mark.asyncio
async def test_table_service_get_demanded_cart_ordering(fast_client: MFFGameClient):
    """Test get_demanded_cart aggregates waiting farmis ordered by highest profitability."""
    tables = TableService(fast_client)
    raw_datablock = {
        "tables": {},
        "farmis": [
            {"id": "1", "status": 0, "price": 1000, "cart": {131: 2}},
            {"id": "2", "status": 0, "price": 5000, "cart": {131: 1, 132: 3}},  # Higher price
            {"id": "3", "status": 1, "price": 9999, "cart": {133: 5}},  # Already seated -> ignore
        ],
    }
    tables.update(raw_datablock)

    demanded_cart = tables.get_demanded_cart()
    # Should aggregate status=0 only: 131: (1 from Farmi 2 + 2 from Farmi 1) = 3; 132: 3
    assert demanded_cart == {131: 3, 132: 3}
    assert 133 not in demanded_cart

    # PIDs list matches keys
    assert tables.get_demanded_pids() == [131, 132]


@pytest.mark.asyncio
async def test_kitchen_produces_only_demanded_dishes(fast_client: MFFGameClient):
    """Test kitchen only cooks demanded dishes (PID 131) and never cooks unneeded ones (PID 132)."""
    fast_client.rid = "test_rid"
    # Stock has ingredients for both recipes, but 0 prepared dishes
    stock = DummyStockService({2: 100, 4: 100, 131: 0, 132: 0})
    kitchen = KitchenService(fast_client, stock)  # type: ignore[arg-type]

    raw_datablock = {
        "buildings": {
            "1": {
                "name": "Pikante Pfanne",
                "level": 1,
                "slots": {
                    "1": {"slot": "1", "pid": None, "remain": 0, "block": 0, "coins": 0},
                    "2": {"slot": "2", "pid": None, "remain": 0, "block": 0, "coins": 0},
                },
            }
        },
        "products": {
            "10": {"pos": 1, "out": {"131": 1}, "in": {"2": 5, "4": 2}},
            "11": {"pos": 1, "out": {"132": 1}, "in": {"2": 5, "4": 2}},
        },
    }
    kitchen.update(raw_datablock)

    production_calls: list[dict] = []

    async def fake_api_call(endpoint: str, params: dict) -> dict:
        if params.get("action") == "production":
            production_calls.append(params)
        return {"datablock": 1}

    fast_client.api_call = fake_api_call  # type: ignore[method-assign]

    # Farmi demands 1x PID 131. Buffer is 0 to isolate farmi demand.
    cooked = await kitchen.produce(demanded_cart={131: 1}, reserve_buffer=0)

    assert cooked == 1
    assert len(production_calls) == 1
    # Only recipe 10 (PID 131) was cooked
    assert production_calls[0]["id"] == 10
    # Slot 2 was left free, PID 132 was NOT cooked!
    assert kitchen.buildings[1].slots[2].is_free is True


@pytest.mark.asyncio
async def test_kitchen_multi_slot_demand(fast_client: MFFGameClient):
    """Test multiple slots are used when Farmi demand exceeds 1 batch."""
    fast_client.rid = "test_rid"
    stock = DummyStockService({2: 100, 4: 100, 131: 0})
    kitchen = KitchenService(fast_client, stock)  # type: ignore[arg-type]

    raw_datablock = {
        "buildings": {
            "1": {
                "name": "Pikante Pfanne",
                "level": 1,
                "slots": {
                    "1": {"slot": "1", "pid": None, "remain": 0, "block": 0, "coins": 0},
                    "2": {"slot": "2", "pid": None, "remain": 0, "block": 0, "coins": 0},
                    "3": {"slot": "3", "pid": None, "remain": 0, "block": 0, "coins": 0},
                },
            }
        },
        "products": {
            "10": {"pos": 1, "out": {"131": 1}, "in": {"2": 5, "4": 2}},
        },
    }
    kitchen.update(raw_datablock)

    production_calls: list[dict] = []

    async def fake_api_call(endpoint: str, params: dict) -> dict:
        if params.get("action") == "production":
            production_calls.append(params)
        return {"datablock": 1}

    fast_client.api_call = fake_api_call  # type: ignore[method-assign]

    # Waiting Farmis need 3x PID 131. All 3 slots should be used!
    cooked = await kitchen.produce(demanded_cart={131: 3}, reserve_buffer=0)
    assert cooked == 3
    assert len(production_calls) == 3
    for call in production_calls:
        assert call["id"] == 10


@pytest.mark.asyncio
async def test_no_blind_cooking_when_no_demand_and_buffer_satisfied(fast_client: MFFGameClient):
    """Test slots stay free when no Farmi demand exists and buffer is satisfied (no Priority 3 blind cooking)."""
    fast_client.rid = "test_rid"
    # Ingredients available, and stock has 50x PID 131
    stock = DummyStockService({2: 100, 4: 100, 131: 50})
    kitchen = KitchenService(fast_client, stock)  # type: ignore[arg-type]

    raw_datablock = {
        "buildings": {
            "1": {
                "name": "Pikante Pfanne",
                "level": 1,
                "slots": {
                    "1": {"slot": "1", "pid": None, "remain": 0, "block": 0, "coins": 0},
                },
            }
        },
        "products": {
            "10": {"pos": 1, "out": {"131": 1}, "in": {"2": 5, "4": 2}},
        },
    }
    kitchen.update(raw_datablock)

    production_calls: list[dict] = []

    async def fake_api_call(endpoint: str, params: dict) -> dict:
        if params.get("action") == "production":
            production_calls.append(params)
        return {"datablock": 1}

    fast_client.api_call = fake_api_call  # type: ignore[method-assign]

    # No Farmi demands, buffer is 50 (stock already has 50)
    cooked = await kitchen.produce(demanded_cart={}, reserve_buffer=50)
    assert cooked == 0
    assert len(production_calls) == 0
    assert kitchen.buildings[1].slots[1].is_free is True


@pytest.mark.asyncio
async def test_kitchen_service_grasps_missing_ingredients_for_farmi(fast_client: MFFGameClient):
    """Test that KitchenService automatically grasps missing ingredients when Farmi demands Pommes mit Ketchup."""
    fast_client.rid = "test_rid"
    # Farmi wants PID 141 (Pommes mit Ketchup). Stock has 2x PID 141 (deficit: 2).
    # Recipe 12 needs 100x Kartoffeln (PID 26) and 3x Ketchup (PID 144).
    # Stock only has 88x Kartoffeln (PID 26) and 100x Ketchup (PID 144).
    stock = DummyStockService({26: 88, 144: 100, 141: 2})

    grasped_calls: list[list[dict]] = []

    async def fake_grasp(reqs: list[dict]) -> bool:
        grasped_calls.append(reqs)
        # Simulate successful procurement of missing potatoes
        for r in reqs:
            pid = r["pid"]
            needed = r["amount"]
            if stock.get_amount(pid) < needed:
                stock.inventory[pid] = needed + 500
        return True

    stock.grasp_products = fake_grasp  # type: ignore[method-assign]

    kitchen = KitchenService(fast_client, stock)  # type: ignore[arg-type]

    raw_datablock = {
        "buildings": {
            "2": {
                "name": "Imbissbude",
                "level": 3,
                "slots": {
                    "1": {"slot": "1", "pid": None, "remain": 0, "block": 0, "coins": 0},
                },
            }
        },
        "products": {
            "12": {
                "pos": 2,
                "out": {"141": 3},
                "in": {"26": 100, "144": 3},
                "star": 5,
            },
        },
    }
    kitchen.update(raw_datablock)

    production_calls: list[dict] = []

    async def fake_api_call(endpoint: str, params: dict) -> dict:
        if params.get("action") == "production":
            production_calls.append(params)
        return {"datablock": 1}

    fast_client.api_call = fake_api_call  # type: ignore[method-assign]

    # Test 1: with auto_buy_ingredients=False, missing potatoes are NOT bought and production does NOT start
    cooked_disabled = await kitchen.produce(demanded_cart={141: 4}, auto_buy_ingredients=False)
    assert cooked_disabled == 0
    assert len(production_calls) == 0
    assert len(grasped_calls) == 0

    # Test 2: with auto_buy_ingredients=True, potatoes are grasped and production starts!
    cooked = await kitchen.produce(demanded_cart={141: 4}, auto_buy_ingredients=True)
    assert cooked == 1
    assert len(grasped_calls) == 1
    assert any(r["pid"] == 26 and r["amount"] >= 100 for r in grasped_calls[0])
    assert len(production_calls) == 1
    assert production_calls[0]["action"] == "production"
    assert production_calls[0]["table"] == 2
    assert production_calls[0]["chair"] == 1
    assert production_calls[0]["id"] == 12  # Recipe 12 = Pommes mit Ketchup


@pytest.mark.asyncio
async def test_foodworld_service_aborts_export_on_market_full(fast_client: MFFGameClient):
    """Test FoodworldService stops exporting surplus dishes when the 20 offers limit is reached."""
    fast_client.rid = "test_rid"
    # Two surplus fw dishes: PID 131 and PID 132
    stock = DummyStockService({131: 50, 132: 50})

    market_mock = AsyncMock()
    market_mock.get_offers = AsyncMock(return_value=[])  # Empty market

    config = FoodworldConfig(dish_reserve_buffer=20, only_empty_market=True)
    fw_service = FoodworldService(
        client=fast_client,
        stock_service=stock,  # type: ignore[arg-type]
        market_service=market_mock,
        config=config,
    )

    market_calls: list[dict] = []

    async def fake_api_call(endpoint: str, params: dict) -> dict:
        market_calls.append(params)
        return {
            "0": 0,
            "1": "Mehr als 20 Angebote gleichzeitig am Markt sind nicht möglich.",
        }

    fast_client.api_call = fake_api_call  # type: ignore[method-assign]

    exported = await fw_service._export_surplus_dishes()
    assert exported == 0
    # Should have stopped after first offer attempt (PID 131) and NOT proceeded to PID 132
    assert len(market_calls) == 1
    assert market_calls[0]["pid"] == 131



