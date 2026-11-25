import httpx
import pytest
import respx
from fastapi.testclient import TestClient

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
)
from app.modules.spicehouse.oven import SpiceOvenService
from app.modules.spicehouse.solver import SpiceQuestSolver
from app.services.stock_service import StockService


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
    for slot_map in plan.values():
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
    for slot_map in plan_fallback.values():
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
            return_value=httpx.Response(
                200, json={"status": "ok", "datablock": 1, "updateblock": {}}
            )
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
    # Pfeffer getrocknet (1110): 520 units (surplus = 20 > 500 reserve) -> needed for Quest 6 (1120)
    # Zimt getrocknet (1111): 580 units (surplus = 80 > 500 reserve)
    stock_svc.farm_stocks = {
        10: {
            1110: 520,
            1111: 580,
            1120: 0,
        },
        1: {
            1120: 0,
        },
    }
    stock_svc.products = {
        1110: Product(pid=1110, name="Pfeffer getrocknet", amount=520),
        1111: Product(pid=1111, name="Zimt getrocknet", amount=580),
        1120: Product(pid=1120, name="Pfeffer gemahlen", amount=0),
    }

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(
                200, json={"status": "ok", "datablock": 1, "updateblock": {}}
            )
        )

        # Scenario A: Quest 6 demands Pfeffer gemahlen (1120)
        # Slot 1 should pick 1110 first (10 units), Slot 2 should pick another 10 units of 1110
        started = await mill.produce(
            stock_service=stock_svc,
            quest_solver=solver,
            quest_demands={1120: 30},
        )
        assert started == 2
        assert stock_svc.products[1110].amount == 500  # 520 - 20 = 500 (reserve reached)

        # Scenario B: Next cycle, 1110 is down to reserve (500), only 1111 (Zimt getrocknet) has surplus -> fallback
        mill.slots = {1: MillSlotInfo(slot=1, capacity=10, remain=0, pid=None)}
        started_fallback = await mill.produce(
            stock_service=stock_svc,
            quest_solver=solver,
            quest_demands={},
        )
        assert started_fallback == 1
        assert stock_svc.products[1111].amount == 570  # 580 - 10 = 570


@pytest.mark.asyncio
async def test_customer_collection():
    """Verify that satisfied customers are served and rewarded."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    client.rid = "test_rid"
    cust_svc = SpiceCustomerService(client, farm_id=10)

    cust_svc.customers = {
        1: SpiceCustomer(id="c1", slot=1, data={1120: 5}),  # Satisfied (510 - 500 = 10 >= 5)
        2: SpiceCustomer(id="c2", slot=2, data={1121: 50}),  # Insufficient stock (505 - 500 = 5 < 50)
    }

    stock_svc = StockService(client)
    stock_svc.farm_stocks = {
        10: {
            1120: 510,
            1121: 505,
        }
    }
    stock_svc.products = {
        1120: Product(pid=1120, name="Pfeffer gemahlen", amount=510),
        1121: Product(pid=1121, name="Zimt gemahlen", amount=505),
    }

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(
                200, json={"status": "ok", "datablock": 1, "updateblock": {}}
            )
        )

        accepted = await cust_svc.collect(stock_service=stock_svc)
        assert accepted == 1
        # Stock of 1120 should be deducted by 5 (510 - 5 = 505)
        assert stock_svc.products[1120].amount == 505


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
    assert "min_dried_reserve" in data
    assert "min_ground_reserve" in data

    # 2. PUT /api/v1/spicehouse/settings
    update_res = client.put(
        "/api/v1/spicehouse/settings",
        json={
            "enabled": True,
            "auto_oven": True,
            "auto_mill": True,
            "auto_customer": True,
            "min_crop_reserve": 300,
            "min_dried_reserve": 400,
            "min_ground_reserve": 450,
        },
        headers=headers,
    )
    assert update_res.status_code == 200
    updated_cfg = update_res.json()["config"]
    assert updated_cfg["auto_mill"] is True
    assert updated_cfg["min_crop_reserve"] == 300
    assert updated_cfg["min_dried_reserve"] == 400
    assert updated_cfg["min_ground_reserve"] == 450

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
            1110: 600,  # 600x Pfeffer getrocknet available to refill (surplus = 100 > 500 reserve)
            1125: 0,
        }
    }
    stock_svc.products = {
        1110: Product(pid=1110, name="Pfeffer getrocknet", amount=600),
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

        # Mock clear_mill API for sort switch
        respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/farm.php",
            params__contains={"mode": "spicehouse_clear_mill", "slot": "3"},
        ).mock(
            return_value=httpx.Response(
                200,
                json={"status": "ok", "datablock": 1, "updateblock": {}},
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
        assert stock_svc.farm_stocks[10][1110] == 550  # 600 - 50 = 550


@pytest.mark.asyncio
async def test_mill_respects_min_dried_reserve():
    """Verify that mill only grinds dried spices exceeding min_dried_reserve."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    client.rid = "test_rid"
    mill = SpiceMillService(client, farm_id=10)
    solver = SpiceQuestSolver(farm_id=10)

    # Slot with capacity 10
    mill.slots = {1: MillSlotInfo(slot=1, capacity=10, remain=0, pid=None)}

    stock_svc = StockService(client)
    # 500 units = exactly at reserve -> surplus = 0 -> nothing should be milled
    stock_svc.farm_stocks = {10: {1110: 500}}
    stock_svc.products = {1110: Product(pid=1110, name="Pfeffer getrocknet", amount=500)}

    started = await mill.produce(
        stock_service=stock_svc,
        quest_solver=solver,
        quest_demands={},
        min_dried_reserve=500,
    )
    assert started == 0
    assert stock_svc.products[1110].amount == 500

    # Increase stock to 506 units -> surplus = 6 (< slot capacity 10)
    # Mill should take exactly 6 units, leaving 500 in stock!
    stock_svc.farm_stocks = {10: {1110: 506}}
    stock_svc.products = {1110: Product(pid=1110, name="Pfeffer getrocknet", amount=506)}

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(
                200, json={"status": "ok", "datablock": 1, "updateblock": {}}
            )
        )
        started_partial = await mill.produce(
            stock_service=stock_svc,
            quest_solver=solver,
            quest_demands={},
            min_dried_reserve=500,
        )
        assert started_partial == 1
        assert mill.slots[1].amount == 6
        assert stock_svc.products[1110].amount == 500


@pytest.mark.asyncio
async def test_customer_respects_all_spice_reserves():
    """Verify customer service enforces raw, dried, and ground reserves."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    client.rid = "test_rid"
    cust_svc = SpiceCustomerService(client, farm_id=10)

    # Customer 1 wants 20 Pfeffer (raw 1100)
    # Customer 2 wants 15 Pfeffer getrocknet (dried 1110)
    # Customer 3 wants 10 Pfeffer gemahlen (ground 1120)
    cust_svc.customers = {
        1: SpiceCustomer(id="c1", slot=1, data={1100: 20}),
        2: SpiceCustomer(id="c2", slot=2, data={1110: 15}),
        3: SpiceCustomer(id="c3", slot=3, data={1120: 10}),
    }

    stock_svc = StockService(client)
    # Raw 1100: 510 (surplus 10 < 20 needed) -> Rejected
    # Dried 1110: 520 (surplus 20 >= 15 needed) -> Accepted
    # Ground 1120: 505 (surplus 5 < 10 needed) -> Rejected
    stock_svc.farm_stocks = {
        10: {
            1100: 510,
            1110: 520,
            1120: 505,
        }
    }
    stock_svc.products = {
        1100: Product(pid=1100, name="Pfeffer", amount=510),
        1110: Product(pid=1110, name="Pfeffer getrocknet", amount=520),
        1120: Product(pid=1120, name="Pfeffer gemahlen", amount=505),
    }

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(
                200, json={"status": "ok", "datablock": 1, "updateblock": {}}
            )
        )

        accepted = await cust_svc.collect(
            stock_service=stock_svc,
            min_crop_reserve=500,
            min_dried_reserve=500,
            min_ground_reserve=500,
        )
        # Only customer 2 should be accepted
        assert accepted == 1
        assert stock_svc.products[1100].amount == 510  # Unchanged
        assert stock_svc.products[1110].amount == 505  # 520 - 15 = 505
        assert stock_svc.products[1120].amount == 505  # Unchanged


@pytest.mark.asyncio
async def test_mill_balancing_lowest_ground_stock():
    """Verify mills prioritize dried spices whose ground stock is lowest (Least-Stock-First)."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    client.rid = "test_rid"
    mill = SpiceMillService(client, farm_id=10)
    solver = SpiceQuestSolver(farm_id=10)

    mill.slots = {
        1: MillSlotInfo(slot=1, capacity=50, remain=0, pid=None),
        2: MillSlotInfo(slot=2, capacity=50, remain=0, pid=None),
        3: MillSlotInfo(slot=3, capacity=50, remain=0, pid=None),
    }

    stock_svc = StockService(client)
    # Pfeffer getrocknet (1110) has huge surplus (500), but Pfeffer gemahlen (1120) is already at 7947!
    # Piment getrocknet (1115) has 100 surplus, Piment gemahlen (1125) is only 162.
    # Kardamom getrocknet (1113) has 100 surplus, Kardamom gemahlen (1123) is 480.
    stock_svc.farm_stocks = {
        10: {
            1110: 1000,
            1113: 600,
            1115: 600,
            1120: 7947,
            1123: 480,
            1125: 162,
        },
        1: {},
    }
    stock_svc.products = {
        1110: Product(pid=1110, name="Pfeffer getrocknet", amount=1000),
        1113: Product(pid=1113, name="Kardamom getrocknet", amount=600),
        1115: Product(pid=1115, name="Piment getrocknet", amount=600),
        1120: Product(pid=1120, name="Pfeffer gemahlen", amount=7947),
        1123: Product(pid=1123, name="Kardamom gemahlen", amount=480),
        1125: Product(pid=1125, name="Piment gemahlen", amount=162),
    }

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(
                200, json={"status": "ok", "datablock": 1, "updateblock": {}}
            )
        )

        started = await mill.produce(
            stock_service=stock_svc,
            quest_solver=solver,
            quest_demands={},
            min_dried_reserve=500,
        )
        assert started == 3
        # Slot 1 & 2 should grind Piment (1115: 162 -> 212 -> 262, surplus exhausted)
        # Slot 3 should grind Kardamom (1113: 480 -> 530); Pfeffer (7947) is skipped!
        assert mill.slots[1].pid == 1115
        assert mill.slots[2].pid == 1115
        assert mill.slots[3].pid == 1113
        assert stock_svc.farm_stocks[10][1110] == 1000


def test_oven_balancing_lowest_downstream_stock():
    """Verify oven fallback prioritizes raw spices with lowest downstream (dried + ground) stock."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    oven = SpiceOvenService(client, farm_id=10)
    solver = SpiceQuestSolver(farm_id=10)

    oven.lines = {
        1: OvenSlotInfo(line=1, capacity=50),
        2: OvenSlotInfo(line=2, capacity=50),
    }

    stock_svc = StockService(client)
    # Pfeffer raw (1100): 1500 (surplus 1000), downstream = 500 + 7947 = 8447
    # Piment raw (1105): 560 (surplus 60), downstream = 0 + 162 = 162
    # Nelke raw (1104): 600 (surplus 100), downstream = 129 + 576 = 705
    stock_svc.farm_stocks = {
        10: {
            1100: 1500,
            1104: 600,
            1105: 560,
            1110: 500,
            1114: 129,
            1115: 0,
            1120: 7947,
            1124: 576,
            1125: 162,
        },
        1: {},
    }

    plan = oven.plan_oven_loading(
        stock_service=stock_svc,
        quest_solver=solver,
        quest_demands={},
        min_crop_reserve=500,
        min_dried_reserve=500,
    )
    # Line 1 must take 50x Piment (1105); Line 2 takes remaining 10x Piment + 40x Nelke (1104)
    assert plan["1"]["1"] == {"pid": 1105, "amount": 50}
    assert plan["2"]["1"] == {"pid": 1105, "amount": 10}
    assert plan["2"]["2"] == {"pid": 1104, "amount": 40}


def test_farm10_agriculture_spice_balancing():
    """Verify Farm 10 PlantStrategySolver balances full spice chain (raw + dried + ground)."""
    from app.modules.agriculture.strategies import PlantStrategySolver

    client = MFFGameClient(server=1, username="test", password="pwd")
    stock_svc = StockService(client)
    stock_svc.products = {
        1100: Product(pid=1100, name="Pfeffer", category="spice", amount=0),
        1101: Product(pid=1101, name="Zimt", category="spice", amount=0),
        1103: Product(pid=1103, name="Kardamom", category="spice", amount=0),
        1105: Product(pid=1105, name="Piment", category="spice", amount=0),
        1106: Product(pid=1106, name="Sternanis", category="spice", amount=0),
    }

    # Case 1: Zimt raw is below reserve (440 < 500) -> reserve protection picks Zimt first!
    stock_svc.farm_stocks = {
        10: {
            1100: 680,
            1101: 440,
            1103: 500,
            1105: 500,
            1106: 0,  # 0 seeds -> must be skipped
            1110: 500,
            1111: 510,
            1113: 0,
            1115: 0,
            1120: 7947,
            1121: 3695,
            1123: 480,
            1125: 162,
        },
        1: {},
    }
    candidates = PlantStrategySolver.resolve_candidates(
        strategy_name="plantQuest",
        stock_service=stock_svc,
        category="spice",
        min_products=500,
        quest_requirements={4: 350000},  # Non-spice quest
        farm_id=10,
    )
    assert [p.pid for p in candidates] == [1101, 1105, 1103, 1100]

    # Case 2: All seeded spices are >= 500 raw -> lowest full-chain stock (Piment: 662) wins!
    stock_svc.farm_stocks[10][1101] = 500
    candidates_balanced = PlantStrategySolver.resolve_candidates(
        strategy_name="plantQuest",
        stock_service=stock_svc,
        category="spice",
        min_products=500,
        quest_requirements={4: 350000},
        farm_id=10,
    )
    assert [p.pid for p in candidates_balanced] == [1105, 1103, 1101, 1100]


@pytest.mark.asyncio
async def test_oven_harvest_dict_and_immediate_refill_and_mill_cycle():
    """Verify oven harvest handles dict datablock, updates dried stock, and immediately refills oven + mills."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    client.rid = "test_rid"
    oven = SpiceOvenService(client, farm_id=10)
    mill = SpiceMillService(client, farm_id=10)
    solver = SpiceQuestSolver(farm_id=10)

    oven.lines = {1: OvenSlotInfo(line=1, capacity=60)}
    oven.oven_remain = 0
    oven.oven_is_ready = True
    oven.oven_is_idle = False

    mill.slots = {1: MillSlotInfo(slot=1, level=6, capacity=50, pid=None, amount=0, output=0, remain=0)}

    stock_svc = StockService(client)
    # Raw Piment (1105): 600 (surplus 100), Dried Piment (1115): 500 (at reserve -> 0 surplus before harvest!)
    stock_svc.farm_stocks = {10: {1105: 600, 1115: 500, 1125: 100}, 1: {}}
    stock_svc.products = {
        1105: Product(pid=1105, name="Piment", amount=600),
        1115: Product(pid=1115, name="Piment getrocknet", amount=500),
        1125: Product(pid=1125, name="Piment gemahlen", amount=100),
    }

    with respx.mock:
        # Upstream spicehouse_open_oven returns a dict datablock with harvested dried spices
        respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/farm.php",
            params__contains={"mode": "spicehouse_open_oven"},
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "ok",
                    "datablock": {"1115": 60, "spicehouse_points": 100},
                    "updateblock": {},
                },
            )
        )
        respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/farm.php",
            params__contains={"mode": "spicehouse_start_oven"},
        ).mock(
            return_value=httpx.Response(
                200, json={"status": "ok", "datablock": 1, "updateblock": {}}
            )
        )
        respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/farm.php",
            params__contains={"mode": "spicehouse_set_millslot"},
        ).mock(
            return_value=httpx.Response(
                200, json={"status": "ok", "datablock": 1, "updateblock": {}}
            )
        )

        # 1. Harvest oven -> must succeed, set idle=True, and add 60x 1115 to Farm 10 stock (500 -> 560)
        harvested = await oven.harvest(stock_service=stock_svc)
        assert harvested is True
        assert oven.oven_is_idle is True
        assert oven.oven_is_ready is False
        assert stock_svc.farm_stocks[10][1115] == 560

        # 2. Immediately reload oven in the same cycle
        restarted = await oven.produce(
            stock_service=stock_svc,
            quest_solver=solver,
            quest_demands={},
            min_crop_reserve=500,
            min_dried_reserve=500,
        )
        assert restarted is True
        assert oven.oven_is_idle is False
        assert stock_svc.farm_stocks[10][1105] == 540

        # 3. Immediately start mill with the freshly dried Piment (560 - 500 = 60 surplus -> loads 50)
        mills_started = await mill.produce(
            stock_service=stock_svc,
            quest_solver=solver,
            quest_demands={},
            min_dried_reserve=500,
        )
        assert mills_started == 1
        assert mill.slots[1].pid == 1115
        assert mill.slots[1].amount == 50
        assert stock_svc.farm_stocks[10][1115] == 510


@pytest.mark.asyncio
async def test_serve_order_customers_before_oven_and_mill_produce():
    """Verify SpicehouseService.serve() harvests first, serves customers BEFORE oven/mill consume surpluses."""
    from app.modules.spicehouse.service import SpicehouseService

    client = MFFGameClient(server=1, username="test", password="pwd")
    client.rid = "test_rid"
    sh = SpicehouseService(client, farm_id=10)

    stock_svc = StockService(client)
    # Raw Piment (1105): 600 (100 surplus) -> Customer needs 94!
    # Dried Nelke (1114): 550 (50 surplus) -> Customer needs 46!
    # If oven/mill ran before customer, they would consume 1105 and 1114 down to 500 and starve the customer.
    stock_svc.farm_stocks = {10: {1105: 600, 1114: 550}, 1: {}}
    stock_svc.products = {
        1105: Product(pid=1105, name="Piment", amount=600),
        1114: Product(pid=1114, name="Nelke getrocknet", amount=550),
    }

    with respx.mock:
        respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/farm.php",
            params__contains={"mode": "spicehouse_init"},
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "ok",
                    "updateblock": {
                        "spicehouse": {
                            "data": {
                                "level": 10,
                                "points": 1000,
                                "oven": 0,
                                "oven_slots": {"1": {"level": 6}},
                                "mill_slots": {
                                    "1": {"level": 6, "pid": 0, "amount": 0, "output": 0}
                                },
                                "customers": {
                                    "1": {
                                        "id": "254180",
                                        "data": {"1105": 94, "1114": 46},
                                        "reward": {
                                            "spicehouse_points": 1000,
                                            "money": 25200,
                                            "points": 36800,
                                        },
                                    }
                                },
                            },
                            "config": {
                                "oven_levels": {"6": {"capacity": 60}},
                                "mill_levels": {"6": {"capacity": 50}},
                            },
                        }
                    },
                },
            )
        )
        respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/farm.php",
            params__contains={"mode": "spicehouse_accept_customer", "slot": "1"},
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "ok",
                    "datablock": {"spicehouse_points": 1000, "money": 25200},
                    "updateblock": {},
                },
            )
        )
        respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/farm.php",
            params__contains={"mode": "spicehouse_start_oven"},
        ).mock(
            return_value=httpx.Response(
                200, json={"status": "ok", "datablock": 1, "updateblock": {}}
            )
        )
        respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/farm.php",
            params__contains={"mode": "spicehouse_set_millslot"},
        ).mock(
            return_value=httpx.Response(
                200, json={"status": "ok", "datablock": 1, "updateblock": {}}
            )
        )

        await sh.serve(stock_service=stock_svc)

        # Customer 1 must have been served BEFORE oven/mill consumed the surplus!
        assert 1 not in sh.customer.customers
        # Remaining 6x Piment (600 - 94 = 506 -> 6 surplus) loaded into oven, leaving 500
        assert stock_svc.farm_stocks[10][1105] == 500
        # Remaining 4x Nelke getrocknet (550 - 46 = 504 -> 4 surplus) loaded into mill, leaving 500
        assert stock_svc.farm_stocks[10][1114] == 500
