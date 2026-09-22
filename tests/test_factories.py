from unittest.mock import MagicMock

import pytest
import respx
from httpx import Response

from app.core.client import MFFGameClient
from app.models.factory import FactorySlot
from app.models.product import Product
from app.modules.farm_buildings.factory import Factory
from app.services.stock_service import StockService


@pytest.fixture
def mock_client():
    client = MFFGameClient(server=21, username="test", password="pw")
    client.rid = "mock_rid_factory"
    return client


@pytest.fixture
def mock_stock_service(mock_client):
    service = StockService(client=mock_client)
    service.products = {
        10: Product(pid=10, name="Kuhmilch", category="v", amount=100),
        27: Product(pid=27, name="Käse", category="v", amount=20),
        110: Product(pid=110, name="Ziegenmilch", category="v", amount=100),
        111: Product(pid=111, name="Ziegenkäse", category="v", amount=80),
        2: Product(pid=2, name="Mais", category="v", amount=5000),
        116: Product(pid=116, name="Maisöl", category="v", amount=200),
        43: Product(pid=43, name="Walnüsse", category="v", amount=500),
        121: Product(pid=121, name="Walnussöl", category="v", amount=10),
        11: Product(pid=11, name="Schafwolle", category="v", amount=100),
        28: Product(pid=28, name="Wollknäuel", category="v", amount=5),
        151: Product(pid=151, name="Angorawolle", category="v", amount=500),
        152: Product(pid=152, name="Angoragarn", category="v", amount=0),
    }
    # Setup farm racks: Farm 5 has 0 Wolle (11), but 50 Angorawolle (151)
    service.farm_stocks = {
        1: {10: 100, 27: 20, 110: 100, 111: 80, 2: 5000, 116: 200, 43: 500, 121: 10, 11: 100, 28: 5},
        5: {11: 0, 151: 50, 152: 0},
    }
    return service


def test_factory_slot_parsing_and_models(mock_client):
    """Verify slots with empty list [], active remain, ready, and block: 1 states."""
    factory = Factory(client=mock_client, farm_id=2, position=5, building_id=13, name="Ölpresse")

    mock_data = {
        "datablock": [
            1,
            {
                "farm": "2",
                "position": 5,
                "buildingid": "13",
                "level": "1",
                "products": {
                    "1": [0, [[2, 100]], [116, 10], 500, 3600, 31, 13],
                },
                "slots": {
                    "1": {"pid": "121", "ready": 1, "amount": 35},
                    "2": {"pid": "116", "remain": 1800, "amount": 10},
                    "3": {"block": 1, "cost": [[2, 1], 0, [5, 172800]]},
                    "4": [],
                },
            },
        ]
    }

    catalog = {
        116: Product(pid=116, name="Maisöl", category="v"),
        121: Product(pid=121, name="Walnussöl", category="v"),
    }

    ok = pytest.importorskip("asyncio").run(factory.update(body=mock_data, catalog=catalog))
    assert ok is True
    assert len(factory.slots) == 4

    # Slot 1: Ready to harvest
    s1 = factory.slots[1]
    assert s1.is_ready is True
    assert s1.is_empty is False
    assert s1.pid == 121
    assert s1.amount == 35

    # Slot 2: Active production
    s2 = factory.slots[2]
    assert s2.is_active is True
    assert s2.is_ready is False
    assert s2.remain_seconds == 1800

    # Slot 3: Blocked (Coin rental)
    s3 = factory.slots[3]
    assert s3.is_blocked is True
    assert s3.is_empty is False

    # Slot 4: Empty slot (represented as empty list in PHP)
    s4 = factory.slots[4]
    assert s4.is_empty is True
    assert s4.is_blocked is False
    assert s4.is_ready is False


def test_factory_recipe_selection_quest_priority(mock_client, mock_stock_service):
    """Verify that a product needed by an active quest is prioritized over min stock."""
    factory = Factory(client=mock_client, farm_id=2, position=1, building_id=8, name="Käserei")

    mock_data = {
        "datablock": [
            1,
            {
                "farm": "2",
                "position": 1,
                "buildingid": "8",
                "level": "5",
                "products": {
                    "1": [0, [[10, 12]], [27, 15], 4500, 96000, 18, 8],  # Käse (Stock: 20)
                    "2": [0, [[110, 5]], [111, 7], 4900, 47232, 18, 8],  # Ziegenkäse (Stock: 80)
                },
                "slots": {"1": []},
            },
        ]
    }
    pytest.importorskip("asyncio").run(factory.update(body=mock_data))

    # Quest needs Käse (27)
    quest_reqs = {27: 100}  # Deficit = 100 - 20 = 80
    chosen = factory.select_recipe(
        stock_service=mock_stock_service,
        quest_requirements=quest_reqs,
    )
    assert chosen is not None
    assert chosen.output_pid == 27
    assert chosen.item_id == "1"


def test_factory_recipe_selection_lowest_stock(mock_client, mock_stock_service):
    """Verify that without quests, the recipe with lowest output stock is selected."""
    factory = Factory(client=mock_client, farm_id=2, position=5, building_id=13, name="Ölpresse")

    mock_data = {
        "datablock": [
            1,
            {
                "farm": "2",
                "position": 5,
                "buildingid": "13",
                "level": "1",
                "products": {
                    "1": [0, [[2, 1980]], [116, 35], 890, 50400, 31, 13],  # Maisöl (Stock: 200)
                    "6": [0, [[43, 100]], [121, 35], 3400, 151200, 36, 13],  # Walnussöl (Stock: 10)
                },
                "slots": {"1": []},
            },
        ]
    }
    pytest.importorskip("asyncio").run(factory.update(body=mock_data))

    chosen = factory.select_recipe(stock_service=mock_stock_service)
    assert chosen is not None
    # Walnussöl has stock 10 < Maisöl stock 200 -> choose Walnussöl
    assert chosen.output_pid == 121
    assert chosen.item_id == "6"


def test_factory_farm5_strict_local_shelf(mock_client, mock_stock_service):
    """Verify that for farm >= 5, ingredients must be present in that farm's rack (no Farm 1 fallback)."""
    factory = Factory(client=mock_client, farm_id=5, position=5, building_id=9, name="Wollspinnerei")

    mock_data = {
        "datablock": [
            1,
            {
                "farm": "5",
                "position": 5,
                "buildingid": "9",
                "level": "4",
                "products": {
                    # Recipe 1: Schafwolle 11 (Main stock has 100, but Farm 5 has 0!)
                    "1": [0, [[11, 8]], [28, 24], 5000, 153000, 27, 9],
                    # Recipe 2: Angorawolle 151 (Farm 5 has 50 >= 5!)
                    "2": [0, [[151, 5]], [152, 20], 7750, 165240, 38, 9],
                },
                "slots": {"1": []},
            },
        ]
    }
    pytest.importorskip("asyncio").run(factory.update(body=mock_data))

    chosen = factory.select_recipe(stock_service=mock_stock_service)
    assert chosen is not None
    # Must NOT choose Recipe 1 (Schafwolle 11 is 0 on Farm 5)
    # Must choose Recipe 2 (Angoragarn 152)
    assert chosen.output_pid == 152
    assert chosen.item_id == "2"


@pytest.mark.asyncio
async def test_factory_harvest_and_produce(mock_client, mock_stock_service):
    """Verify harvesting a ready slot and producing in an empty slot."""
    factory = Factory(client=mock_client, farm_id=2, position=5, building_id=13, name="Ölpresse")

    init_data = {
        "datablock": [
            1,
            {
                "farm": "2",
                "position": 5,
                "buildingid": "13",
                "level": "1",
                "products": {
                    "6": [0, [[43, 100]], [121, 35], 3400, 151200, 36, 13],
                },
                "slots": {
                    "1": {"pid": "121", "ready": 1, "amount": 35},
                    "2": [],
                    "3": {"block": 1},
                },
            },
        ]
    }

    with respx.mock:
        # Mock innerinfos
        respx.get(url__startswith="https://s21.myfreefarm.de/ajax/farm.php").mock(
            return_value=Response(200, json=init_data)
        )

        await factory.update()
        assert factory.slots[1].is_ready is True
        assert factory.slots[2].is_empty is True

        # Mock harvestproduction for slot 1
        respx.get(url__startswith="https://s21.myfreefarm.de/ajax/farm.php").mock(
            return_value=Response(200, json={"status": "ok"})
        )

        # Harvest slot 1
        h_ok = await factory.harvest(slot_id=1, stock_service=mock_stock_service)
        assert h_ok is True
        assert factory.slots[1].is_empty is True
        assert factory.slots[1].is_ready is False

        # Mock start for slot 2
        respx.get(url__startswith="https://s21.myfreefarm.de/ajax/farm.php").mock(
            return_value=Response(200, json={"status": "ok"})
        )

        recipe = factory.recipes["6"]
        initial_walnuts = mock_stock_service.get_amount(43)
        p_ok = await factory.produce(slot_id=2, recipe=recipe, stock_service=mock_stock_service)
        assert p_ok is True
        assert factory.slots[2].is_empty is False
        assert factory.slots[2].pid == 121
        # 100 walnuts deducted
        assert mock_stock_service.get_amount(43) == initial_walnuts - 100



@pytest.mark.asyncio
async def test_factories_api_endpoints():
    """Verify GET /api/v1/factories and settings endpoints."""
    from fastapi.testclient import TestClient

    from app.core.auth import create_access_token
    from app.main import app
    from app.worker.scheduler import worker_scheduler

    # Setup dummy factory on farm_service
    mock_f_client = MFFGameClient(server=21, username="test", password="pw")
    factory = Factory(client=mock_f_client, farm_id=2, position=5, building_id=13, name="Ölpresse")
    factory.slots[1] = FactorySlot(slot_id=1, pid=121, is_ready=True, amount=35)

    mock_farm_svc = MagicMock()
    mock_farm_svc.factories = [factory]
    worker_scheduler.last_farm_service = mock_farm_svc

    token = create_access_token({"sub": "admin"})
    client = TestClient(app)

    # 1. GET /api/v1/factories
    res = client.get("/api/v1/factories", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["name"] == "Ölpresse"
    assert data[0]["building_id"] == 13
    assert data[0]["slots"][0]["is_ready"] is True

    # 2. GET /api/v1/factories/settings
    res_s = client.get("/api/v1/factories/settings", headers={"Authorization": f"Bearer {token}"})
    assert res_s.status_code == 200
    assert res_s.json()["enabled"] is True

    # 3. PUT /api/v1/factories/settings
    res_put = client.put(
        "/api/v1/factories/settings",
        json={"enabled": True, "auto_harvest": False, "auto_produce": True, "coin_protection": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res_put.status_code == 200
    assert res_put.json()["auto_harvest"] is False
