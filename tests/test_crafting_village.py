from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import create_access_token
from app.core.client import MFFGameClient
from app.main import app
from app.modules.crafting_village.service import CraftingVillageService
from app.worker.scheduler import worker_scheduler


@pytest.fixture
def auth_headers() -> dict[str, str]:
    token = create_access_token(data={"sub": "mff", "role": "admin"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def dummy_client() -> MFFGameClient:
    return MFFGameClient(server=21)


MOCK_MINE_INIT = {
    "status": "ok",
    "datablock": {
        "data": {
            "id": "16",
            "unr": "17626250",
            "workers_slots": None,
            "slots_slots": None,
            "stock": {"sandstone": 5, "granite": 2},
            "tutorial": {"start": 1792696077},
            "createdate": "1792484170",
            "level": 1,
            "workers": {
                "16": {
                    "id": "16",
                    "unr": "17626250",
                    "position": "1",
                    "name": "Skyler",
                    "energy": "80",
                    "data": {
                        "sandstone": {
                            "points": 22,
                            "level": 3,
                            "percent": 13.333333333333334,
                        }
                    },
                    "productionid": "1873",
                    "createdate": "1792484170",
                }
            },
            "items": {
                "1304": {
                    "id": "1304",
                    "unr": "17626250",
                    "name": "pickaxe",
                    "durability": "19",
                    "stock": "1",
                },
                "1321": {
                    "id": "1321",
                    "unr": "17626250",
                    "name": "shoes",
                    "durability": "19",
                    "stock": "0",
                },
            },
            "productions": {
                "1": {
                    "1": {
                        "id": "1873",
                        "unr": "17626250",
                        "slot": "1",
                        "position": "1",
                        "name": "sandstone",
                        "amount": "7",
                        "workers": {"1": 16},
                        "items": {"1": 1321},
                        "duration": "18000",
                        "remain": 16000,
                    }
                }
            },
            "validenergyproducts": {"1": 1, "2": 2},
            "stats": {
                "harvest_all": "10",
                "harvest_sandstone": "10",
                "mining_started": "3",
            },
        },
        "config": {
            "constants": {
                "worker_energy": 100,
                "refill_products_points_energy": 10,
            },
            "material": {
                "sandstone": {
                    "name": "Sandstein",
                    "duration": 18000,
                    "harvest": 5,
                    "energy": 10,
                    "worker": 1,
                    "items": 2,
                    "reward": {"points": 200, "money": 150, "xp": 5},
                },
                "granite": {
                    "name": "Granit",
                    "duration": 21600,
                    "harvest": 5,
                    "energy": 15,
                    "worker": 1,
                    "items": 2,
                    "reward": {"points": 100, "money": 300, "xp": 5},
                },
            },
            "items": {
                "pickaxe": {
                    "name": "Goldene Spitzhacke",
                    "money": 4000,
                    "durability": 20,
                    "bonus": {"xp": 0.4},
                }
            },
        },
    },
}


@pytest.mark.asyncio
async def test_crafting_village_models_and_parsing(dummy_client: MFFGameClient):
    """Verify that CraftingVillageService correctly parses mine_init data."""
    dummy_client.api_call = AsyncMock(return_value=MOCK_MINE_INIT)

    service = CraftingVillageService(client=dummy_client)
    snapshot = await service.init_remote()

    assert snapshot is not None
    assert snapshot.mine is not None
    mine = snapshot.mine

    # Mine status
    assert mine.level == 1
    assert mine.stock["sandstone"] == 5
    assert mine.stock["granite"] == 2

    # Workers
    assert "16" in mine.workers
    worker = mine.workers["16"]
    assert worker.name == "Skyler"
    assert worker.energy == 80
    assert worker.is_working is True
    assert worker.energy_percent == 80.0
    assert "sandstone" in worker.data
    assert worker.data["sandstone"].level == 3

    # Items
    assert "1304" in mine.items
    pickaxe = mine.items["1304"]
    assert pickaxe.name == "pickaxe"
    assert pickaxe.durability == 19
    assert pickaxe.stock == 1
    assert pickaxe.is_in_use is False
    assert pickaxe.is_available is True

    shoes = mine.items["1321"]
    assert shoes.is_in_use is True

    # Productions
    assert "1" in mine.productions
    prod = mine.productions["1"]
    assert prod.name == "sandstone"
    assert prod.amount == 7
    assert prod.remain == 16000
    assert prod.is_ready is False

    # Summary
    summary = service.get_summary()
    assert summary.active is True
    assert summary.mine_level == 1
    assert summary.workers_count == 1
    assert summary.working_workers_count == 1
    assert summary.active_productions_count == 1
    assert summary.ready_productions_count == 0
    assert summary.sandstone_stock == 5
    assert summary.granite_stock == 2


@pytest.mark.asyncio
async def test_crafting_village_api_endpoints(
    dummy_client: MFFGameClient,
    auth_headers: dict[str, str],
):
    """Test REST API endpoints for Handwerkerdorf."""
    dummy_client.api_call = AsyncMock(return_value=MOCK_MINE_INIT)
    service = CraftingVillageService(client=dummy_client)
    await service.init_remote()
    worker_scheduler.last_crafting_village_service = service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. GET /api/v1/crafting
        res = await ac.get("/api/v1/crafting", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert "config" in data
        assert "summary" in data
        assert "snapshot" in data
        assert data["summary"]["active"] is True
        assert data["summary"]["sandstone_stock"] == 5

        # 2. GET /api/v1/crafting/settings
        res_cfg = await ac.get("/api/v1/crafting/settings", headers=auth_headers)
        assert res_cfg.status_code == 200
        cfg_data = res_cfg.json()
        assert "enabled" in cfg_data
        assert "preferred_material" in cfg_data

        # 3. PUT /api/v1/crafting/settings
        update_payload = {
            "enabled": True,
            "auto_harvest": True,
            "auto_mine": True,
            "preferred_material": "granite",
        }
        res_put = await ac.put(
            "/api/v1/crafting/settings",
            headers=auth_headers,
            json=update_payload,
        )
        assert res_put.status_code == 200
        put_data = res_put.json()
        assert put_data["status"] == "ok"
        assert put_data["settings"]["preferred_material"] == "granite"
