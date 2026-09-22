
import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.auth import create_access_token
from app.main import app
from app.models.product import Product
from app.models.vehicle import (
    VehicleConfigData,
    VehicleRouteConfig,
    VehiclesConfig,
    VehicleState,
)
from app.modules.agriculture.vehicle import Vehicle, VehicleService, calculate_speed_score
from app.services.stock_service import StockService

api_test_client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_fast_client(fast_client):
    fast_client.rid = "test_rid"


@pytest.fixture
def mock_map_data():
    return {
        "vehicles": {
            "1": {
                "1": {"current": 1, "route": 1, "type": 1, "remain": 0},
                "4": {"current": 1, "route": 1, "type": 4, "remain": 0},
            },
            "2": {
                "9": {"current": 6, "route": 2, "type": 9, "remain": 0},
            },
            "4": {
                "19": {"current": 10, "route": 4, "type": 19, "remain": 0},
            },
        },
        "config": {
            "vehicles": {
                "1": {"name": "Handwagen", "capacity": 100, "products": 1, "farms": [5], "duration": 7200},
                "4": {"name": "Traktor", "capacity": 500, "products": 2, "farms": [5], "duration": 1800},
                "9": {"name": "Pickup", "capacity": 1000, "products": 3, "farms": [6], "duration": 900},
                "19": {"name": "Transporter", "capacity": 1500, "products": 3, "farms": [10], "duration": 600},
            }
        },
    }


def test_speed_score_calculation():
    """Verify that vehicles with shorter travel duration or higher tier receive higher speed score."""
    v1 = VehicleConfigData(name="Handwagen", capacity=100, products=1, duration=7200)
    v4 = VehicleConfigData(name="Traktor", capacity=500, products=2, duration=1800)
    v9 = VehicleConfigData(name="Pickup", capacity=1000, products=3, duration=900)

    score_1 = calculate_speed_score(v1, 1)
    score_4 = calculate_speed_score(v4, 4)
    score_9 = calculate_speed_score(v9, 9)

    assert score_9 > score_4 > score_1


def test_vehicle_fastest_selection(fast_client, mock_map_data):
    """Verify VehicleService automatically selects the fastest available vehicle on Route 1 (Traktor vs Handwagen)."""
    cfg = VehiclesConfig(
        enabled=True,
        routes={
            5: VehicleRouteConfig(
                farm_id=5,
                route=1,
                auto_fastest=True,
                transport=True,
            )
        },
    )
    svc = VehicleService(fast_client, config=cfg)
    svc.update(mock_map_data)

    assert 1 in svc.vehicles
    selected = svc.vehicles[1]
    assert selected.vehicle_id == 4
    assert selected.name == "Traktor"


@pytest.mark.asyncio
async def test_vehicle_main_to_outer_with_supplies(fast_client):
    """Test Leg 1: At Farm 1, vehicle loads Kohlrabi (< 4000) and dispatches map_sendvehicle."""
    stock = StockService(fast_client)
    kohlrabi = Product(pid=4, name="Kohlrabi", category="v", amount=1000, tmp_amount=0)
    stock.products = {4: kohlrabi}
    stock.farm_temp_stocks = {5: {4: 100}}  # Outer farm has only 100 units (< 4000)

    state = VehicleState(current=1, route=1, vehicle_type=4, remain=0)
    config = VehicleConfigData(name="Traktor", capacity=500, products=2, farms=[5], duration=1800)
    route_cfg = VehicleRouteConfig(farm_id=5, route=1, required_products=[4], transport=True)

    v = Vehicle(fast_client, state, config, route_cfg)

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(
                200,
                json={"datablock": 1, "updateblock": {"map": {"vehicles": {"1": {"4": {"current": 5, "remain": 1800}}}}}},
            )
        )
        res = await v.loop(stock)
        assert res is True
        assert v.last_sent_cart == "1,4,500_"
        assert stock.products[4].amount == 500  # 1000 - 500 deducted


@pytest.mark.asyncio
async def test_vehicle_main_to_outer_empty_cart_when_transport_active(fast_client):
    """Test Leg 1: At Farm 1, no supplies needed, but transport=True -> sends empty cart to pick up harvest."""
    stock = StockService(fast_client)
    state = VehicleState(current=1, route=2, vehicle_type=9, remain=0)
    config = VehicleConfigData(name="Pickup", capacity=1000, products=3, farms=[6], duration=900)
    route_cfg = VehicleRouteConfig(farm_id=6, route=2, required_products=[], transport=True)

    v = Vehicle(fast_client, state, config, route_cfg)

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": 1})
        )
        res = await v.loop(stock)
        assert res is True
        assert v.last_sent_cart == ""


@pytest.mark.asyncio
async def test_vehicle_outer_to_main_quest_prioritization(fast_client):
    """Test Leg 2: Quest-demanded crop (Limette) is prioritized into Slot 1 before Ananas with larger surplus."""
    stock = StockService(fast_client)
    ananas = Product(pid=351, name="Ananas", category="ex", amount=500, tmp_amount=0)
    limette = Product(pid=352, name="Limette", category="ex", amount=10, tmp_amount=0)
    stock.products = {351: ananas, 352: limette}

    # On Farm 5 rack: Ananas has 600 (surplus: 600 - 120 = 480). Limette has 200 (surplus: 200 - 120 = 80).
    stock.farm_temp_stocks = {5: {351: 600, 352: 200}}

    # Quest needs 50 Limette (main stock is 10, deficit is 40)
    quest_reqs = {352: 50}

    state = VehicleState(current=5, route=1, vehicle_type=4, remain=0)
    config = VehicleConfigData(name="Traktor", capacity=500, products=2, farms=[5], duration=1800)
    route_cfg = VehicleRouteConfig(farm_id=5, route=1, transport=True, prioritize_quests=True)

    v = Vehicle(fast_client, state, config, route_cfg)

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": 1})
        )
        res = await v.loop(stock, quest_requirements=quest_reqs)
        assert res is True
        # Slot 1 must contain Limette (Prio 1 Quest), Slot 2 contains Ananas (Prio 2 Surplus)
        # Limette: 80 loaded. Remaining capacity: 500 - 80 = 420. Ananas: 420 loaded!
        assert v.last_sent_cart == "1,352,80_2,351,420_"
        # Check seed reserve was preserved: 600 - 420 = 180 remaining (> 120)
        assert stock.get_temp_amount(5, 351) == 180
        assert stock.get_temp_amount(5, 352) == 120


@pytest.mark.asyncio
async def test_vehicle_outer_to_main_farm10_only_milled_surplus(fast_client):
    """Test Leg 2: On Farm 10, general harvest surplus only accepts milled products ("gemahlen"). Raw spices remain."""
    stock = StockService(fast_client)
    raw_pepper = Product(pid=1100, name="Pfeffer", category="spice", amount=100)
    ground_pepper = Product(pid=1103, name="Gemahlener Pfeffer", category="spice", amount=50)
    stock.products = {1100: raw_pepper, 1103: ground_pepper}

    # On Farm 10 rack: 500 raw Pfeffer (surplus 380), 300 Gemahlener Pfeffer (surplus 180)
    stock.farm_temp_stocks = {10: {1100: 500, 1103: 300}}

    state = VehicleState(current=10, route=4, vehicle_type=19, remain=0)
    config = VehicleConfigData(name="Transporter", capacity=1500, products=3, farms=[10], duration=600)
    route_cfg = VehicleRouteConfig(farm_id=10, route=4, transport=True, send_partial=True, only_milled_surplus=True)

    v = Vehicle(fast_client, state, config, route_cfg)

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": 1})
        )
        res = await v.loop(stock)
        assert res is True
        # Cart must only contain Gemahlener Pfeffer (180), NOT raw Pfeffer!
        assert v.last_sent_cart == "1,1103,180_"
        # Raw pepper untouched
        assert stock.get_temp_amount(10, 1100) == 500


@pytest.mark.asyncio
async def test_vehicle_outer_departure_on_quest_satisfied(fast_client):
    """Test Leg 2: Partial load departs immediately if it satisfies an active quest deficit."""
    stock = StockService(fast_client)
    limette = Product(pid=352, name="Limette", category="ex", amount=0)
    stock.products = {352: limette}
    # Only 150 Limette on Farm 5 rack (surplus: 150 - 120 = 30)
    stock.farm_temp_stocks = {5: {352: 150}}

    quest_reqs = {352: 30}  # Quest deficit: 30. Loaded: 30 -> satisfies deficit!

    state = VehicleState(current=5, route=1, vehicle_type=4, remain=0)
    config = VehicleConfigData(name="Traktor", capacity=500, products=2, farms=[5])
    route_cfg = VehicleRouteConfig(farm_id=5, route=1, transport=True, send_partial=False)

    v = Vehicle(fast_client, state, config, route_cfg)

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": 1})
        )
        res = await v.loop(stock, quest_requirements=quest_reqs)
        assert res is True
        assert v.last_sent_cart == "1,352,30_"


@pytest.mark.asyncio
async def test_vehicle_outer_wait_when_partial_and_not_urgent(fast_client):
    """Test Leg 2: Partial load without quest need and with sufficient supplies (>= 500) waits at outer farm."""
    stock = StockService(fast_client)
    ananas = Product(pid=351, name="Ananas", category="ex", amount=100)
    kohlrabi = Product(pid=4, name="Kohlrabi", category="v", amount=100)
    stock.products = {351: ananas, 4: kohlrabi}

    # Only 220 Ananas (surplus 100 < capacity 500)
    stock.farm_temp_stocks = {5: {351: 220, 4: 1200}}

    state = VehicleState(current=5, route=1, vehicle_type=4, remain=0)
    config = VehicleConfigData(name="Traktor", capacity=500, products=2, farms=[5])
    route_cfg = VehicleRouteConfig(
        farm_id=5,
        route=1,
        transport=True,
        required_products=[4],
        send_partial=False,
    )

    v = Vehicle(fast_client, state, config, route_cfg)

    res = await v.loop(stock)
    # Should NOT send
    assert res is False
    assert v.last_sent_cart == ""


@pytest.mark.asyncio
async def test_vehicle_outer_urgent_supplies_triggers_departure(fast_client):
    """Test Leg 2: Partial load departs immediately if outer farm supplies fall below 500."""
    stock = StockService(fast_client)
    ananas = Product(pid=351, name="Ananas", category="ex", amount=100)
    kohlrabi = Product(pid=4, name="Kohlrabi", category="v", amount=100)
    stock.products = {351: ananas, 4: kohlrabi}

    # Ananas surplus 100, but Kohlrabi is critically low at 150 (< 500)
    stock.farm_temp_stocks = {5: {351: 220, 4: 150}}

    state = VehicleState(current=5, route=1, vehicle_type=4, remain=0)
    config = VehicleConfigData(name="Traktor", capacity=500, products=2, farms=[5])
    route_cfg = VehicleRouteConfig(
        farm_id=5,
        route=1,
        transport=True,
        required_products=[4],
        send_partial=False,
    )

    v = Vehicle(fast_client, state, config, route_cfg)

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": 1})
        )
        res = await v.loop(stock)
        assert res is True
        assert v.last_sent_cart == "1,351,100_"


@pytest.mark.asyncio
async def test_vehicle_in_transit_skips(fast_client):
    """Test vehicle skips action when remaining cooldown > 0."""
    stock = StockService(fast_client)
    state = VehicleState(current=1, route=1, vehicle_type=4, remain=350)
    config = VehicleConfigData(name="Traktor", capacity=500, products=2, farms=[5])
    route_cfg = VehicleRouteConfig(farm_id=5, route=1, transport=True)

    v = Vehicle(fast_client, state, config, route_cfg)
    res = await v.loop(stock)
    assert res is False


def test_vehicle_api_endpoints():
    """Test REST API GET /vehicles and POST /vehicles/{route}/send."""
    token = create_access_token({"sub": "mff", "role": "admin"})
    headers = {"Authorization": f"Bearer {token}"}

    res = api_test_client.get("/api/v1/vehicles", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert any(v["route_id"] == 1 for v in data)


@pytest.mark.asyncio
async def test_farm8_only_quest_products_and_crop_reserve(fast_client):
    """Verify Farm 8 only transports products demanded by active quests and respects 500 crop reserve."""
    stock = StockService(fast_client)
    stock.products = {
        957: Product(pid=957, name="Brunnenkresse", category="water", size_x=1, size_y=1, amount=0),
        950: Product(pid=950, name="Reis", category="water", size_x=1, size_y=1, amount=0),
        953: Product(pid=953, name="Taro-Wurzel", category="water", size_x=1, size_y=1, amount=0),
    }
    # Stocks on Farm 8:
    # 957: 1200 units (in quest) -> reserve 500 -> surplus 700
    # 950: 2000 units (NOT in quest) -> should be skipped entirely
    # 953: 400 units (in quest) -> below 500 reserve -> surplus 0
    stock.farm_temp_stocks = {
        8: {
            957: 1200,
            950: 2000,
            953: 400,
        }
    }

    state = VehicleState(current=8, route=4, vehicle_type=19, remain=0)
    config = VehicleConfigData(name="Wasserhelikopter", capacity=750, products=5, farms=[8])
    route_cfg = VehicleRouteConfig(
        farm_id=8,
        route=4,
        transport=True,
        prioritize_quests=True,
        only_quest_products=True,
        min_crop_reserve=500,
        send_partial=True,
    )

    v = Vehicle(fast_client, state, config, route_cfg)
    quest_requirements = {957: 500, 953: 200}  # 957 and 953 are needed

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": 1})
        )
        res = await v.loop(stock, quest_requirements=quest_requirements)
        assert res is True
        # Cart should only contain 957 with max 700 (1200 - 500 reserve)
        assert "957" in v.last_sent_cart
        assert "950" not in v.last_sent_cart  # non-quest skipped
        assert "953" not in v.last_sent_cart  # below reserve skipped
        assert v.current_cargo[0].pid == 957
        assert v.current_cargo[0].amount == 700


@pytest.mark.asyncio
async def test_farm8_sushi_supply_threshold_500(fast_client):
    """Verify Farm 1 supplies Sushi-Bar ingredients only when Farm 8 stock < 500."""
    stock = StockService(fast_client)
    # PIDs: 12 (Honig), 17 (Karotten), 9 (Eier)
    stock.products = {
        12: Product(pid=12, name="Honig", category="e", amount=1000),
        17: Product(pid=17, name="Karotten", category="v", amount=2000),
        9: Product(pid=9, name="Eier", category="e", amount=3000),
    }
    # Farm 8 stock:
    # 12: 450 (< 500) -> send!
    # 17: 350 (< 500) -> send!
    # 9: 600 (>= 500) -> skip!
    stock.farm_temp_stocks = {
        8: {
            12: 450,
            17: 350,
            9: 600,
        }
    }

    state = VehicleState(current=1, route=4, vehicle_type=19, remain=0)
    config = VehicleConfigData(name="Wasserhelikopter", capacity=750, products=5, farms=[8])
    route_cfg = VehicleRouteConfig(
        farm_id=8,
        route=4,
        transport=True,
        sushi_supply=True,
        sushi_reserve_threshold=500,
    )

    v = Vehicle(fast_client, state, config, route_cfg)

    # Mock sushibar service with recipes needing 12, 17, and 9
    class MockRecipe:
        def __init__(self, needs, is_coin=False, level=1):
            self.needs = needs
            self.is_coin_recipe = is_coin
            self.level = level

    class MockSushiBar:
        level = 5
        recipes = {
            1: MockRecipe({12: 5, 17: 10}),
            2: MockRecipe({9: 8}),
        }

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": 1})
        )
        res = await v.loop(stock, sushibar_service=MockSushiBar())
        assert res is True
        # 17 (350) and 12 (450) must be loaded; 9 (600) must NOT be loaded
        assert "17" in v.last_sent_cart
        assert "12" in v.last_sent_cart
        assert ",9," not in v.last_sent_cart
        loaded_pids = {c.pid for c in v.current_cargo}
        assert 17 in loaded_pids
        assert 12 in loaded_pids
        assert 9 not in loaded_pids


@pytest.mark.asyncio
async def test_farm8_no_supply_when_all_above_500(fast_client):
    """Verify vehicle stays on Farm 1 if all Sushi ingredients on Farm 8 are >= 500 and no quest goods waiting."""
    stock = StockService(fast_client)
    stock.products = {
        12: Product(pid=12, name="Honig", category="e", amount=1000),
        17: Product(pid=17, name="Karotten", category="v", amount=2000),
    }
    stock.farm_temp_stocks = {
        8: {
            12: 550,
            17: 600,
        }
    }

    state = VehicleState(current=1, route=4, vehicle_type=19, remain=0)
    config = VehicleConfigData(name="Wasserhelikopter", capacity=750, products=5, farms=[8])
    route_cfg = VehicleRouteConfig(
        farm_id=8,
        route=4,
        transport=True,
        sushi_supply=True,
        sushi_reserve_threshold=500,
        only_quest_products=True,
    )

    v = Vehicle(fast_client, state, config, route_cfg)

    class MockRecipe:
        needs = {12: 5, 17: 10}
        is_coin_recipe = False
        level = 1

    class MockSushiBar:
        level = 5
        recipes = {1: MockRecipe()}

    # No quest requirements
    res = await v.loop(stock, quest_requirements={}, sushibar_service=MockSushiBar())
    assert res is False
    assert v.last_sent_cart == ""


def test_vehicle_config_update_api_farm8():
    """Test updating Farm 8 vehicle configuration via PUT /api/v1/vehicles/8/config."""
    token = create_access_token({"sub": "mff", "role": "admin"})
    headers = {"Authorization": f"Bearer {token}"}

    update_payload = {
        "transport": True,
        "only_quest_products": True,
        "sushi_supply": True,
        "sushi_reserve_threshold": 500,
        "min_crop_reserve": 500,
    }
    res = api_test_client.put("/api/v1/vehicles/8/config", json=update_payload, headers=headers)
    assert res.status_code == 200
    cfg = res.json()["route_config"]
    assert cfg["transport"] is True
    assert cfg["only_quest_products"] is True
    assert cfg["sushi_supply"] is True
    assert cfg["sushi_reserve_threshold"] == 500
    assert cfg["min_crop_reserve"] == 500


@pytest.mark.asyncio
async def test_farm8_never_loads_other_farm_products(fast_client):
    """Verify Farm 8 Wasserhelikopter NEVER loads products from other farm categories (e.g. Melisse from Farm 6)."""
    stock = StockService(fast_client)
    melisse = Product(pid=708, name="Melisse", category="alpin", amount=0, tmp_amount=30)
    enzian = Product(pid=705, name="Enzian", category="alpin", amount=0, tmp_amount=100)
    brunnenkresse = Product(pid=957, name="Brunnenkresse", category="water", amount=0, tmp_amount=400)
    stock.products = {708: melisse, 705: enzian, 957: brunnenkresse}
    stock.farm_temp_stocks = {
        6: {708: 30, 705: 100},
        8: {957: 400},
    }

    # Verify stock_service does not leak Farm 6 temp stock to Farm 8
    assert stock.get_temp_amount(8, 708) == 0
    assert stock.get_temp_amount(6, 708) == 30

    state = VehicleState(current=8, route=4, vehicle_type=19, remain=0)
    config = VehicleConfigData(name="Wasserhelikopter", capacity=750, products=5, farms=[8])
    route_cfg = VehicleRouteConfig(
        farm_id=8,
        route=4,
        transport=True,
        prioritize_quests=True,
        only_quest_products=True,
        min_crop_reserve=500,
    )

    v = Vehicle(fast_client, state, config, route_cfg)
    # Active Quest 4 demands Melisse and Enzian
    quest_requirements = {708: 36619, 705: 117180}

    res = await v.loop(stock, quest_requirements=quest_requirements)
    # Must not send vehicle with Melisse
    assert res is False
    assert v.current_cargo == []
    assert v.last_sent_cart == ""

    # Also test when vehicle is at Farm 1: must not dispatch empty to Farm 8 for Melisse
    state_farm1 = VehicleState(current=1, route=4, vehicle_type=19, remain=0)
    v_farm1 = Vehicle(fast_client, state_farm1, config, route_cfg)
    res_farm1 = await v_farm1.loop(stock, quest_requirements=quest_requirements)
    assert res_farm1 is False
    assert v_farm1.last_sent_cart == ""


@pytest.mark.asyncio
async def test_farm8_transports_quest5_water_products(fast_client):
    """Verify Farm 8 Wasserhelikopter transports water products needed for Questreihe 5 when surplus > 500."""
    stock = StockService(fast_client)
    brunnenkresse = Product(pid=957, name="Brunnenkresse", category="water", size_x=1, size_y=1, amount=100)
    reis = Product(pid=950, name="Reis", category="water", size_x=2, size_y=1, amount=500)
    stock.products = {957: brunnenkresse, 950: reis}
    stock.farm_temp_stocks = {
        8: {957: 1200, 950: 1000},
    }

    state = VehicleState(current=8, route=4, vehicle_type=19, remain=0)
    config = VehicleConfigData(name="Wasserhelikopter", capacity=750, products=5, farms=[8])
    route_cfg = VehicleRouteConfig(
        farm_id=8,
        route=4,
        transport=True,
        prioritize_quests=True,
        only_quest_products=True,
        min_crop_reserve=500,
        send_partial=True,
    )

    v = Vehicle(fast_client, state, config, route_cfg)
    # Active Quest 5 requires 7884 Brunnenkresse
    quest_requirements = {957: 7884}

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": 1})
        )
        res = await v.loop(stock, quest_requirements=quest_requirements)
        assert res is True
        assert "957" in v.last_sent_cart
        assert "950" not in v.last_sent_cart
        assert v.current_cargo[0].pid == 957
        assert v.current_cargo[0].amount == 700


@pytest.mark.asyncio
async def test_vehicle_quest_chronological_priority_order(fast_client):
    """Verify that earlier quests (e.g. Q68 Brunnenkresse) take precedence over later quests (e.g. Q70 Wasserspinat)."""
    stock = StockService(fast_client)
    # PID 957: Brunnenkresse (Quest 68, smaller deficit: 1215)
    # PID 952: Wasserspinat (Quest 70, larger deficit: 3676)
    stock.products = {
        957: Product(pid=957, name="Brunnenkresse", category="water", size_x=1, size_y=1, amount=6669),
        952: Product(pid=952, name="Wasserspinat", category="water", size_x=1, size_y=1, amount=4323),
    }
    # Both crops have surplus on Farm 8:
    # Brunnenkresse: 7169 - 500 = 6669 surplus
    # Wasserspinat: 4823 - 500 = 4323 surplus
    stock.farm_temp_stocks = {
        8: {957: 7169, 952: 4823},
    }

    state = VehicleState(current=8, route=4, vehicle_type=19, remain=0)
    config = VehicleConfigData(name="Wasserhelikopter", capacity=750, products=1, farms=[8])
    route_cfg = VehicleRouteConfig(
        farm_id=8,
        route=4,
        transport=True,
        prioritize_quests=True,
        only_quest_products=True,
        min_crop_reserve=500,
        send_partial=True,
    )

    v = Vehicle(fast_client, state, config, route_cfg)
    quest_requirements = {957: 7884, 952: 7999}
    # Chronological quest ordering: Q68 before Q70
    quest_priority_order = {957: 68, 952: 70}

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": 1})
        )
        res = await v.loop(
            stock,
            quest_requirements=quest_requirements,
            quest_priority_order=quest_priority_order,
        )
        assert res is True
        # Brunnenkresse (Quest 68) must be loaded first despite smaller raw deficit
        assert v.current_cargo[0].pid == 957
        assert v.current_cargo[0].amount == 750
        assert v.last_sent_cart == "1,957,750_"


@pytest.mark.asyncio
async def test_vehicle_route4_sushi_supply_does_not_block_water_quest_crop(fast_client):
    """Verify that sushi recipe water ingredients (e.g. Taro PID 953 in Taro-Dampfnudeln)
    are not treated as incoming supplies on Farm 8 and do not block quest logistics to Farm 1."""
    stock = StockService(fast_client)
    taro = Product(pid=953, name="Taro-Wurzel", category="water", size_x=2, size_y=1, amount=0)
    stock.products = {953: taro}
    stock.farm_temp_stocks = {
        8: {953: 7820},
    }

    state = VehicleState(current=8, route=4, vehicle_type=19, remain=0)
    config = VehicleConfigData(name="Wasserhelikopter", capacity=750, products=1, farms=[8])
    route_cfg = VehicleRouteConfig(
        farm_id=8,
        route=4,
        transport=True,
        prioritize_quests=True,
        only_quest_products=True,
        min_crop_reserve=500,
        send_partial=True,
        sushi_supply=True,
    )

    from app.modules.sushibar.models import SushiRecipe
    class MockSushibar:
        level = 14
        recipes = {
            984: SushiRecipe(pid=984, name="Taro-Dampfnudeln", level=13, needs={950: 8, 953: 7, 12: 5}),
        }

    v = Vehicle(fast_client, state, config, route_cfg)
    quest_requirements = {953: 6979}
    quest_priority_order = {953: 66}

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": 1})
        )
        res = await v.loop(
            stock,
            quest_requirements=quest_requirements,
            quest_priority_order=quest_priority_order,
            sushibar_service=MockSushibar(),
        )
        assert res is True
        assert v.current_cargo[0].pid == 953
        assert v.current_cargo[0].amount == 750
        assert v.last_sent_cart == "1,953,750_"


@pytest.mark.asyncio
async def test_vehicle_supplies_wollknaeuel_to_farm_5(fast_client):
    """Test that vehicle on Farm 1 supplies Wollknäuel (PID 28) to Farm 5 when requested."""
    stock = StockService(fast_client)
    kohlrabi = Product(pid=153, name="Kohlrabi", category="v", amount=1000)
    wollknaeuel = Product(pid=28, name="Wollknäuel", category="e", amount=2500)
    stock.products = {153: kohlrabi, 28: wollknaeuel}
    # Kohlrabi is already full (4080 >= 4000), Wollknäuel is 0 on Farm 5
    stock.farm_temp_stocks = {5: {153: 4080, 28: 0}}

    state = VehicleState(current=1, route=1, vehicle_type=4, remain=0)
    config = VehicleConfigData(name="Sportwagen", capacity=1000, products=2, farms=[5], duration=900)
    route_cfg = VehicleRouteConfig(
        farm_id=5,
        route=1,
        vehicle=4,
        required_products=["Kohlrabi", "Wollknäuel"],
        transport=True,
    )

    v = Vehicle(fast_client, state, config, route_cfg)

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(
                200,
                json={"datablock": 1, "updateblock": {"map": {"vehicles": {"1": {"4": {"current": 5, "remain": 900}}}}}},
            )
        )
        res = await v.loop(stock)
        assert res is True
        # Only Wollknäuel needed (Kohlrabi is full), full 1000 loaded
        assert v.last_sent_cart == "1,28,1000_"
        assert stock.products[28].amount == 1500  # 2500 - 1000


@pytest.mark.asyncio
async def test_vehicle_outer_urgent_wollknaeuel_departure(fast_client):
    """Test that vehicle on Farm 5 departs with partial load if Wollknäuel is < 500 on Farm 5."""
    stock = StockService(fast_client)
    ananas = Product(pid=351, name="Ananas", category="ex", amount=100)
    wollknaeuel = Product(pid=28, name="Wollknäuel", category="e", amount=2500)
    stock.products = {351: ananas, 28: wollknaeuel}

    # Ananas partial surplus 200, Wollknäuel is 0 (< 500 threshold)
    stock.farm_temp_stocks = {5: {351: 320, 28: 0}}

    state = VehicleState(current=5, route=1, vehicle_type=4, remain=0)
    config = VehicleConfigData(name="Sportwagen", capacity=1000, products=2, farms=[5], duration=900)
    route_cfg = VehicleRouteConfig(
        farm_id=5,
        route=1,
        vehicle=4,
        required_products=["Wollknäuel"],
        transport=True,
        send_partial=False,
    )

    v = Vehicle(fast_client, state, config, route_cfg)

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": 1})
        )
        res = await v.loop(stock)
        assert res is True
        assert "1,351,200_" in v.last_sent_cart






