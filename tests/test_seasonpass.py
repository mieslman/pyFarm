import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.core.auth import create_access_token
from app.core.client import MFFGameClient
from app.main import app
from app.models.product import Product
from app.modules.seasonpass import SeasonPassConfig, SeasonPassService, SeasonPassSnapshot, SeasonPassTask
from app.modules.seasonpass.handlers.crops import HarvestTaskHandler, PlantTaskHandler, WaterTaskHandler
from app.modules.seasonpass.handlers.defaults import NoOpTaskHandler
from app.modules.seasonpass.handlers.forestry import ForestryHarvestTaskHandler, ForestryPlantTaskHandler
from app.modules.seasonpass.handlers.misc import FarmiTaskHandler, FriendVisitTaskHandler, WeatherTaskHandler
from app.modules.seasonpass.handlers.sheds import StartProductionTaskHandler
from app.modules.seasonpass.task_registry import get_registered_types, get_task_handler
from app.services.stock_service import StockService
from app.worker.scheduler import worker_scheduler

SAMPLE_SEASONPASS_PAYLOAD = {
    "status": "ok",
    "datablock": {
        "data": {
            "id": "3259",
            "name": "season7",
            "points": "3400",
            "remain": 6195222,
            "rewards": {
                "1": {"free": {"time": 1769938736}},
                "4": {"free": {"time": 1770544010}},
            },
            "tasks": [
                {
                    "id": "460860",
                    "type": "startproduction",
                    "data": {"building": 4, "pid": 11, "count": 1, "points": 100, "done": 0},
                    "createdate": "1771369205",
                    "finishdate": "0",
                    "remain": 150828,
                },
                {
                    "id": "460861",
                    "type": "weather",
                    "data": {"count": 1, "points": 100, "done": 0},
                    "createdate": "1771369205",
                    "finishdate": "0",
                    "remain": 150828,
                },
            ],
            "tasksDone": [
                {
                    "id": "460012",
                    "type": "water",
                    "data": {"building": 1, "pid": 0, "count": 5, "points": 100, "done": 16},
                    "createdate": "1771369205",
                    "finishdate": "1771369689",
                }
            ],
        },
        "config": {
            "constants": {
                "levels": {
                    "1": {"points": 500, "free": {"fertilizer": 10}},
                    "4": {"points": 2000, "free": {"money": 5000}},
                    "5": {"points": 2500, "free": {"coins": 2}},
                    "10": {"points": 5000, "free": {"premium": 7}},
                }
            }
        },
    },
}


def test_seasonpass_models():
    """Verify SeasonPassTask and SeasonPassSnapshot parsing."""
    snapshot = SeasonPassSnapshot.from_api(SAMPLE_SEASONPASS_PAYLOAD)
    assert snapshot.season_name == "season7"
    assert snapshot.points == 3400
    assert len(snapshot.pending_tasks) == 2
    assert len(snapshot.completed_tasks) == 1

    # Task 1: startproduction
    t1 = snapshot.pending_tasks[0]
    assert t1.id == "460860"
    assert t1.type == "startproduction"
    assert t1.building == 4
    assert t1.pid == 11
    assert t1.count == 1
    assert t1.done == 0
    assert not t1.is_completed
    assert t1.progress_ratio == 0.0

    # Completed Task
    t_done = snapshot.completed_tasks[0]
    assert t_done.is_completed
    assert t_done.progress_ratio == 1.0

    # Levels
    assert len(snapshot.levels) == 4
    assert snapshot.levels[1].is_claimed is True
    assert snapshot.levels[4].is_claimed is True
    assert snapshot.levels[5].is_claimed is False  # points 3400 >= 2500 -> ready to claim!
    assert snapshot.levels[10].is_claimed is False  # points 3400 < 5000


def test_task_registry_resolution():
    """Verify task registry contains expected handlers."""
    registered = get_registered_types()
    for expected_type in (
        "plant",
        "harvest",
        "water",
        "startproduction",
        "harvestproduction",
        "forestryplant",
        "forestryharvest",
        "forestrywater",
        "foodworldstartproduction",
        "foodworldharvestproduction",
        "farmi",
        "weather",
        "friendvisit",
        "startwindmillproduction",
    ):
        assert expected_type in registered
        handler_cls = get_task_handler(expected_type)
        assert handler_cls is not None
        assert handler_cls is not NoOpTaskHandler

    assert get_task_handler("unknown_custom_task") is None


@pytest.mark.asyncio
async def test_weather_task_execution():
    """Verify WeatherTaskHandler calls weather_init endpoint."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    client.rid = "test_rid"
    task = SeasonPassTask(id="1", type="weather", payload={"count": 1})
    handler = WeatherTaskHandler(task=task, client=client)

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"status": "ok", "datablock": {}})
        )

        success = await handler.run()
        assert success is True


@pytest.mark.asyncio
async def test_friendvisit_task_execution():
    """Verify FriendVisitTaskHandler fetches friends list and visits showcase garden."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    client.rid = "test_rid"
    task = SeasonPassTask(id="2", type="friendvisit", payload={"count": 1})
    handler = FriendVisitTaskHandler(task=task, client=client)

    with respx.mock:
        # 1. friends_init returns list of friends with UNR
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "ok",
                    "datablock": {
                        "friends": {
                            "101": {"unr": "1234567", "name": "BestFriend"},
                        }
                    },
                },
            )
        )

        success = await handler.run()
        assert success is True


@pytest.mark.asyncio
async def test_crops_task_execution():
    """Verify PlantTaskHandler, HarvestTaskHandler, and WaterTaskHandler."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    client.rid = "test_rid"
    stock_svc = StockService(client)
    stock_svc.products = {6: Product(pid=6, name="Kräuter", amount=100)}

    plant_task = SeasonPassTask(id="3", type="plant", payload={"pid": 6, "count": 10})
    plant_handler = PlantTaskHandler(task=plant_task, client=client, stock_service=stock_svc)

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(
                200,
                json={"status": "ok", "datablock": {"1": {}}},
            )
        )

        success = await plant_handler.run()
        assert success is True

    # Harvest task
    harvest_task = SeasonPassTask(id="4", type="harvest", payload={"count": 5})
    harvest_handler = HarvestTaskHandler(task=harvest_task, client=client)
    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"status": "ok", "datablock": {"1": {}}})
        )
        assert await harvest_handler.run() is True

    # Water task
    water_task = SeasonPassTask(id="5", type="water", payload={"count": 5})
    water_handler = WaterTaskHandler(task=water_task, client=client)
    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"status": "ok", "datablock": {"1": {}}})
        )
        assert await water_handler.run() is True


@pytest.mark.asyncio
async def test_forestry_task_execution():
    """Verify ForestryPlantTaskHandler clears slot 1, plants tree and waters."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    client.rid = "test_rid"
    task = SeasonPassTask(id="6", type="forestryplant", payload={"pid": 4, "count": 1})
    handler = ForestryPlantTaskHandler(task=task, client=client)

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/forestry.php").mock(
            return_value=httpx.Response(200, json={"status": "ok", "datablock": 1})
        )

        success = await handler.run()
        assert success is True

    # Harvest task
    h_task = SeasonPassTask(id="7", type="forestryharvest", payload={"count": 1})
    h_handler = ForestryHarvestTaskHandler(task=h_task, client=client)
    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/forestry.php").mock(
            return_value=httpx.Response(200, json={"status": "ok", "datablock": 1})
        )
        assert await h_handler.run() is True


@pytest.mark.asyncio
async def test_farmi_task_execution():
    """Verify FarmiTaskHandler inspects offers and fulfills satisfied offer."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    client.rid = "test_rid"
    stock_svc = StockService(client)
    stock_svc.products = {17: Product(pid=17, name="Karotten", amount=50)}

    task = SeasonPassTask(id="8", type="farmi", payload={"count": 1})
    handler = FarmiTaskHandler(task=task, client=client, stock_service=stock_svc)

    with respx.mock:
        # Mock getfarms returning a farmi offer requiring 10 Karotten (PID 17)
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(
                200,
                json={
                    "status": "ok",
                    "updateblock": {
                        "farmis": [
                            [
                                {
                                    "id": "farmi_999",
                                    "verkauft": "0",
                                    "price": "120.00",
                                    "p1": "17",
                                    "a1": "10",
                                }
                            ]
                        ]
                    },
                },
            )
        )

        success = await handler.run()
        assert success is True


@pytest.mark.asyncio
async def test_reward_claiming_flow():
    """Verify SeasonPassService claims unlocked free tier rewards."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    client.rid = "test_rid"
    service = SeasonPassService(client=client)
    service.snapshot = SeasonPassSnapshot.from_api(SAMPLE_SEASONPASS_PAYLOAD)

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"status": "ok", "datablock": 1})
        )

        # Level 5 requires 2500 points (current: 3400) and is unclaimed
        claimed = await service.claim_available_rewards()
        assert claimed == 1
        assert service.snapshot.levels[5].is_claimed is True


def test_seasonpass_api_endpoints():
    """Verify REST API endpoints for Seasonpass (/api/v1/seasonpass)."""
    token = create_access_token({"sub": "admin"})
    headers = {"Authorization": f"Bearer {token}"}
    test_client = TestClient(app)

    # 1. GET /api/v1/seasonpass/settings
    res = test_client.get("/api/v1/seasonpass/settings", headers=headers)
    assert res.status_code == 200
    cfg_data = res.json()
    assert "enabled" in cfg_data
    assert "auto_claim_rewards" in cfg_data
    assert "preferred_field_farm" in cfg_data

    # 2. PUT /api/v1/seasonpass/settings
    update_res = test_client.put(
        "/api/v1/seasonpass/settings",
        json={
            "enabled": True,
            "auto_claim_rewards": False,
            "preferred_field_farm": 2,
            "preferred_field_pos": 3,
        },
        headers=headers,
    )
    assert update_res.status_code == 200
    updated_cfg = update_res.json()["config"]
    assert updated_cfg["auto_claim_rewards"] is False
    assert updated_cfg["preferred_field_farm"] == 2
    assert updated_cfg["preferred_field_pos"] == 3

    # 3. GET /api/v1/seasonpass status
    status_res = test_client.get("/api/v1/seasonpass", headers=headers)
    assert status_res.status_code == 200
    status_data = status_res.json()
    assert "config" in status_data
    assert "summary" in status_data
