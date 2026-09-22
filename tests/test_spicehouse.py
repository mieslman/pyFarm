import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import settings
from app.core.auth import create_access_token
from app.core.client import MFFGameClient
from app.main import app
from app.models.product import Product
from app.modules.spicehouse.customers import SpiceCustomerService
from app.modules.spicehouse.mill import SpiceMillService
from app.modules.spicehouse.models import (
    SPICE_DRIED_TO_GROUND,
    SPICE_DRIED_TO_RAW,
    SPICE_GROUND_TO_DRIED,
    SPICE_RAW_TO_DRIED,
    MillSlotInfo,
    OvenSlotInfo,
    SpiceCustomer,
    SpicehouseConfig,
)
from app.modules.spicehouse.oven import SpiceOvenService
from app.modules.spicehouse.service import SpicehouseService
from app.modules.spicehouse.solver import SpiceQuestSolver
from app.services.stock_service import StockService
from app.worker.scheduler import worker_scheduler


def test_spicehouse_models():
    """Verify bidirectional mappings and slot model properties."""
    # 1. Bidirectional mappings
    assert SPICE_RAW_TO_DRIED[1100] == 1110  # Pfeffer -> Pfeffer getrocknet
    assert SPICE_DRIED_TO_RAW[1110] == 1100
    assert SPICE_DRIED_TO_GROUND[1110] == 1120  # Pfeffer getrocknet -> Pfeffer gemahlen
    assert SPICE_GROUND_TO_DRIED[1120] == 1110

    # 2. OvenSlotInfo
    oven_free = OvenSlotInfo(line=1, capacity=10)
    assert oven_free.is_available

    oven_coin_expired = OvenSlotInfo(line=4, is_rented=True, remain=0)
    assert not oven_coin_expired.is_available

    oven_coin_active = OvenSlotInfo(line=4, is_rented=True, remain=3600)
    assert oven_coin_active.is_available

    # 3. MillSlotInfo
    mill_free = MillSlotInfo(slot=2, capacity=10)
    assert mill_free.is_available
    assert mill_free.is_idle

    mill_ready = MillSlotInfo(slot=1, pid=1110, output=10, remain=0)
    assert mill_ready.is_ready
    assert mill_ready.is_ready_to_harvest

    mill_running = MillSlotInfo(slot=1, pid=1110, amount=10, remain=500)
    assert not mill_running.is_ready
    assert not mill_running.is_idle

    mill_coin_expired = MillSlotInfo(slot=4, is_rented=True, remain=0)
    assert not mill_coin_expired.is_available

    # 4. SpiceCustomer
    cust_satisfied = SpiceCustomer(id="c1", slot=1, data={1120: 5})
    assert cust_satisfied.is_satisfied({1120: 10})
    assert not cust_satisfied.is_satisfied({1120: 3})


def test_quest_solver_resolution():
    """Verify Quest 6 demand extraction and recursive resolution."""
    solver = SpiceQuestSolver(farm_id=10)

    quest_status_main = {
        "6": {
            "questid": 12,
            "products": {"1120": 40},  # Needs 40 Pfeffer gemahlen
        }
    }

    demands = solver.extract_quest6_demands(quest_status_main=quest_status_main)
    assert demands == {1120: 40}

    # Ground 1120 needs dried 1110
    assert solver.get_required_dried_spice(1120) == 1110
    # Ground 1120 needs raw 1100
    assert solver.get_required_raw_spice(1120) == 1100
    # Dried 1110 needs raw 1100
    assert solver.get_required_raw_spice(1110) == 1100


@pytest.mark.asyncio
async def test_oven_priority_and_fallback():
    """Verify that oven prioritizes Quest 6 demands and falls back to largest stock."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    client.rid = "test_rid"
    oven = SpiceOvenService(client, farm_id=10)
    solver = SpiceQuestSolver(farm_id=10)

    # Initialize oven with 3 idle lines
    oven.lines = {
        1: OvenSlotInfo(line=1, capacity=10),
        2: OvenSlotInfo(line=2, capacity=10),
        3: OvenSlotInfo(line=3, capacity=10),
    }
    oven.oven_remain = 0

    stock_svc = StockService(client)
    # Farm 10 stock:
    # Pfeffer (1100): 600 (surplus = 100 > 500 reserve) -> needed by Quest 6
    # Zimt (1101): 1200 (surplus = 700 > 500 reserve)
    # Nelke (1104): 450 (deficit < 500 reserve)
    stock_svc.farm_stocks = {
        10: {
            1100: 600,
            1101: 1200,
            1104: 450,
            1110: 0,
            1120: 0,
        },
        1: {
            1120: 0,
        },
    }
    stock_svc.products = {
        1100: Product(pid=1100, name="Pfeffer", amount=600),
        1101: Product(pid=1101, name="Zimt", amount=1200),
        1104: Product(pid=1104, name="Nelke", amount=450),
        1110: Product(pid=1110, name="Pfeffer getrocknet", amount=0),
        1120: Product(pid=1120, name="Pfeffer gemahlen", amount=0),
    }

    # Scenario A: Quest 6 demands Pfeffer gemahlen (1120)
    quest_demands = {1120: 50}
    plan = oven.plan_oven_loading(
        stock_service=stock_svc,
        quest_solver=solver,
        quest_demands=quest_demands,
        min_crop_reserve=500,
    )
    # All lines should bake Pfeffer (1100) because 1120 is missing and Pfeffer has surplus
    assert len(plan) == 3
    for line_nr, slot_map in plan.items():
        assert slot_map["1"]["pid"] == 1100
        assert slot_map["1"]["amount"] == 10

    # Scenario B: Quest 6 has no demands -> Fallback to largest surplus (> 500 reserve)
    plan_fallback = oven.plan_oven_loading(
        stock_service=stock_svc,
        quest_solver=solver,
        quest_demands={},
        min_crop_reserve=500,
    )
    # Zimt (1101) has 1200 units, so it must be selected for all lines!
    assert len(plan_fallback) == 3
    for line_nr, slot_map in plan_fallback.items():
        assert slot_map["1"]["pid"] == 1101
        assert slot_map["1"]["amount"] == 10


@pytest.mark.asyncio
async def test_oven_harvest_and_produce_execution():
    """Verify upstream API calls for oven harvest and start."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    client.rid = "test_rid"
    oven = SpiceOvenService(client, farm_id=10)
    solver = SpiceQuestSolver(farm_id=10)

    oven.lines = {1: OvenSlotInfo(line=1, capacity=10)}
    oven.oven_remain = 0
    oven.oven_is_ready = True

    stock_svc = StockService(client)
    stock_svc.farm_stocks = {10: {1101: 1000}}
    stock_svc.products = {
        1101: Product(pid=1101, name="Zimt", amount=1000),
    }

    with respx.mock:
        # Mock harvest call
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"status": "ok", "datablock": 1, "updateblock": {}})
        )

        harvested = await oven.harvest()
        assert harvested is True
        assert oven.oven_is_idle is True

        started = await oven.produce(
            stock_service=stock_svc,
            quest_solver=solver,
            quest_demands={},
            min_crop_reserve=500,
        )
        assert started is True
        # Verify local stock deduction: line 1 capacity 10 -> 1000 - 10 = 990
        assert stock_svc.products[1101].amount == 1000 - 10


@pytest.mark.asyncio
async def test_mill_priority_and_fallback():
    """Verify that spice mill prioritizes Quest 6 dried precursors and falls back to largest stock."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    client.rid = "test_rid"
    mill = SpiceMillService(client, farm_id=10)
    solver = SpiceQuestSolver(farm_id=10)

    mill.slots = {
        1: MillSlotInfo(slot=1, capacity=10, remain=0, pid=None),
        2: MillSlotInfo(slot=2, capacity=10, remain=0, pid=None),
    }

    stock_svc = StockService(client)
    # Farm 10 stock:
    # Pfeffer getrocknet (1110): 20 units -> needed for Quest 6 (1120)
    # Zimt getrocknet (1111): 80 units
    stock_svc.farm_stocks = {
        10: {
            1110: 20,
            1111: 80,
            1120: 0,
        },
        1: {
            1120: 0,
        },
    }
    stock_svc.products = {
        1110: Product(pid=1110, name="Pfeffer getrocknet", amount=20),
        1111: Product(pid=1111, name="Zimt getrocknet", amount=80),
        1120: Product(pid=1120, name="Pfeffer gemahlen", amount=0),
    }

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"status": "ok", "datablock": 1, "updateblock": {}})
        )

        # Scenario A: Quest 6 demands Pfeffer gemahlen (1120)
        # Slot 1 should pick 1110 first (10 units), Slot 2 should pick another 10 units of 1110
        started = await mill.produce(
            stock_service=stock_svc,
            quest_solver=solver,
            quest_demands={1120: 30},
        )
        assert started == 2
        assert stock_svc.products[1110].amount == 0  # 20 - 20 = 0

        # Scenario B: Next cycle, 1110 is empty, only 1111 (Zimt getrocknet) has stock -> fallback
        mill.slots = {1: MillSlotInfo(slot=1, capacity=10, remain=0, pid=None)}
        started_fallback = await mill.produce(
            stock_service=stock_svc,
            quest_solver=solver,
            quest_demands={},
        )
        assert started_fallback == 1
        assert stock_svc.products[1111].amount == 70  # 80 - 10 = 70


@pytest.mark.asyncio
async def test_customer_collection():
    """Verify that satisfied customers are served and rewarded."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    client.rid = "test_rid"
    cust_svc = SpiceCustomerService(client, farm_id=10)

    cust_svc.customers = {
        1: SpiceCustomer(id="c1", slot=1, data={1120: 5}),  # Satisfied
        2: SpiceCustomer(id="c2", slot=2, data={1121: 50}),  # Insufficient stock
    }

    stock_svc = StockService(client)
    stock_svc.farm_stocks = {
        10: {
            1120: 10,
            1121: 5,
        }
    }
    stock_svc.products = {
        1120: Product(pid=1120, name="Pfeffer gemahlen", amount=10),
        1121: Product(pid=1121, name="Zimt gemahlen", amount=5),
    }

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"status": "ok", "datablock": 1, "updateblock": {}})
        )

        accepted = await cust_svc.collect(stock_service=stock_svc)
        assert accepted == 1
        # Stock of 1120 should be deducted by 5
        assert stock_svc.products[1120].amount == 5


def test_spicehouse_api_endpoints():
    """Test REST API endpoints for Gewürzhaus (/api/v1/spicehouse)."""
    token = create_access_token({"sub": "admin"})
    headers = {"Authorization": f"Bearer {token}"}
    client = TestClient(app)

    # 1. GET /api/v1/spicehouse/settings
    res = client.get("/api/v1/spicehouse/settings", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert "enabled" in data
    assert "auto_oven" in data
    assert "auto_mill" in data
    assert "auto_customer" in data

    # 2. PUT /api/v1/spicehouse/settings
    update_res = client.put(
        "/api/v1/spicehouse/settings",
        json={
            "enabled": True,
            "auto_oven": True,
            "auto_mill": True,
            "auto_customer": True,
            "min_crop_reserve": 300,
        },
        headers=headers,
    )
    assert update_res.status_code == 200
    updated_cfg = update_res.json()["config"]
    assert updated_cfg["auto_mill"] is True
    assert updated_cfg["min_crop_reserve"] == 300

    # 3. GET /api/v1/spicehouse (status)
    status_res = client.get("/api/v1/spicehouse", headers=headers)
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert "config" in status_data
    assert "summary" in status_data


def test_mill_output_progress_calculation():
    """Verify that output and remain are correctly calculated from start timestamp and durations."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    mill = SpiceMillService(client, farm_id=10)

    start_ts = 1700000000
    # Case 1: 50 units of Piment getrocknet (PID 1115, 600s each = 30000s total)
    # Server time: 30000s later -> fully finished
    mill.update(
        mill_slots_data={
            "3": {
                "level": 5,
                "pid": 1115,
                "amount": 50,
                "amount_original": 50,
                "start": start_ts,
            }
        },
        server_time=start_ts + 30000,
    )
    slot3 = mill.slots[3]
    assert slot3.output == 50
    assert slot3.remain == 0
    assert slot3.is_ready_to_harvest is True
    assert slot3.is_idle is False

    # Case 2: Partial progress: 1800s later -> 3 units finished (1800 // 600 = 3), 47 remaining
    mill.update(
        mill_slots_data={
            "1": {
                "level": 6,
                "pid": 1115,
                "amount": 50,
                "amount_original": 50,
                "start": start_ts,
            }
        },
        server_time=start_ts + 1800,
    )
    slot1 = mill.slots[1]
    assert slot1.output == 3
    assert slot1.remain == (50 * 600) - 1800
    assert slot1.is_ready_to_harvest is True
    assert slot1.is_idle is False

    # Case 3: Empty slot with residual PID after harvest
    mill.update(
        mill_slots_data={
            "2": {
                "level": 6,
                "pid": 1115,
                "amount": 0,
                "amount_original": 0,
                "start": start_ts + 1800,
            }
        },
        server_time=start_ts + 1800,
    )
    slot2 = mill.slots[2]
    assert slot2.output == 0
    assert slot2.remain == 0
    assert slot2.is_ready_to_harvest is False
    assert slot2.is_idle is True


@pytest.mark.asyncio
async def test_mill_harvest_dict_and_refill_cycle():
    """Verify harvesting with upstream dictionary datablock and immediate refilling."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    client.rid = "test_rid"
    mill = SpiceMillService(client, farm_id=10)
    solver = SpiceQuestSolver(farm_id=10)

    # Slot 3 has 50 finished units
    mill.slots = {
        3: MillSlotInfo(slot=3, level=5, capacity=50, pid=1115, amount=50, output=50, remain=0),
    }

    stock_svc = StockService(client)
    stock_svc.farm_stocks = {
        10: {
            1110: 100,  # 100x Pfeffer getrocknet available to refill
            1125: 0,
        }
    }
    stock_svc.products = {
        1110: Product(pid=1110, name="Pfeffer getrocknet", amount=100),
        1125: Product(pid=1125, name="Piment gemahlen", amount=0),
    }

    with respx.mock:
        # Mock harvest API returning dictionary datablock as observed on game server
        respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/farm.php",
            params__contains={"mode": "spicehouse_harvest_mill", "slot": "3"},
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "ok",
                    "datablock": {"1125": 50, "spicehouse_points": 50},
                    "updateblock": {},
                },
            )
        )

        # Mock set_millslot API for refill
        respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/farm.php",
            params__contains={"mode": "spicehouse_set_millslot", "slot": "3"},
        ).mock(
            return_value=httpx.Response(
                200,
                json={"status": "ok", "datablock": 1, "updateblock": {}},
            )
        )

        # 1. Harvest
        harvested = await mill.harvest(stock_service=stock_svc)
        assert harvested == 1
        assert mill.slots[3].output == 0
        assert mill.slots[3].amount == 0
        assert mill.slots[3].is_idle is True
        # Ground spice was added to stock
        assert stock_svc.farm_stocks[10][1125] == 50

        # 2. Refill (produce)
        started = await mill.produce(
            stock_service=stock_svc,
            quest_solver=solver,
            quest_demands={},
        )
        assert started == 1
        assert mill.slots[3].pid == 1110
        assert mill.slots[3].amount == 50
        assert mill.slots[3].is_idle is False
        assert stock_svc.farm_stocks[10][1110] == 50  # 100 - 50 = 50
