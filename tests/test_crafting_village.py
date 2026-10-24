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


@pytest.mark.asyncio
async def test_tool_selection_and_material_balancing(dummy_client: MFFGameClient):
    """Verify tool ranking by XP bonus and material selection by least stock."""
    # 1. Least stock selection
    mock_data = {
        "status": "ok",
        "datablock": {
            "data": {
                "id": "16",
                "stock": {"sandstone": 10, "granite": 4},
                "workers": {
                    "16": {
                        "id": "16",
                        "name": "Skyler",
                        "energy": "80",
                        "productionid": None,
                        "data": {
                            "sandstone": {"level": 3, "points": 22},
                            "granite": {"level": 1, "points": 5},
                        },
                    }
                },
                "items": {
                    "1": {
                        "id": "1",
                        "name": "headlamp",
                        "durability": "20",
                        "stock": "1",
                    },  # XP 0.2
                    "2": {"id": "2", "name": "shoes", "durability": "15", "stock": "1"},  # XP 0.8
                    "3": {"id": "3", "name": "pickaxe", "durability": "18", "stock": "1"},  # XP 0.4
                    "4": {
                        "id": "4",
                        "name": "walkietalkie",
                        "durability": "10",
                        "stock": "1",
                    },  # XP 1.6
                },
                "productions": {},
            },
            "config": {
                "items": {
                    "headlamp": {"name": "Stirnlampe", "bonus": {"xp": 0.2}},
                    "pickaxe": {"name": "Spitzhacke", "bonus": {"xp": 0.4}},
                    "shoes": {"name": "Schuhe", "bonus": {"xp": 0.8}},
                    "walkietalkie": {"name": "Funkgerät", "bonus": {"xp": 1.6}},
                },
                "material": {
                    "sandstone": {"name": "Sandstein", "energy": 10, "items": 2},
                    "granite": {"name": "Granit", "energy": 15, "items": 2},
                },
                "slots": {
                    "1": {"material": ["sandstone"]},
                    "2": {"material": ["granite"]},
                },
            },
        },
    }

    dummy_client.api_call = AsyncMock(return_value=mock_data)
    service = CraftingVillageService(client=dummy_client)
    await service.init_remote()

    # Sandstone 10 vs Granite 4 -> least stock is granite!
    next_mat = service.select_next_material()
    assert next_mat == "granite"

    # Tool ranking: walkietalkie (1.6) > shoes (0.8) > pickaxe (0.4) > headlamp (0.2)
    best_tools = service.get_best_tools_for_mining(count=2)
    assert len(best_tools) == 2
    assert best_tools[0].id == "4"  # walkietalkie
    assert best_tools[1].id == "2"  # shoes

    # Worker selection
    best_worker = service.get_best_worker_for_material("granite")
    assert best_worker is not None
    assert best_worker.id == "16"


@pytest.mark.asyncio
async def test_finish_and_start_production_in_serve(dummy_client: MFFGameClient):
    """Test full automation cycle: harvest finished slot, then start new production."""
    ready_init = {
        "status": "ok",
        "datablock": {
            "data": {
                "id": "16",
                "stock": {"sandstone": 2, "granite": 10},
                "workers": {
                    "16": {
                        "id": "16",
                        "name": "Skyler",
                        "energy": "80",
                        "productionid": "1873",
                        "data": {"sandstone": {"level": 3, "points": 22}},
                    }
                },
                "items": {
                    "10": {"id": "10", "name": "shoes", "durability": "19", "stock": "1"},
                    "11": {"id": "11", "name": "pickaxe", "durability": "19", "stock": "1"},
                },
                "productions": {
                    "1": {
                        "1": {
                            "id": "1873",
                            "slot": "1",
                            "position": "1",
                            "name": "sandstone",
                            "amount": "7",
                            "workers": {"1": 16},
                            "items": {"1": 10, "2": 11},
                            "duration": "18000",
                            "remain": -50,  # Ready!
                        }
                    }
                },
            },
            "config": {
                "items": {
                    "pickaxe": {"name": "Spitzhacke", "bonus": {"xp": 0.4}},
                    "shoes": {"name": "Schuhe", "bonus": {"xp": 0.8}},
                },
                "material": {
                    "sandstone": {"name": "Sandstein", "energy": 10, "items": 2},
                    "granite": {"name": "Granit", "energy": 15, "items": 2},
                },
            },
        },
    }

    finish_resp = {
        "status": "ok",
        "datablock": {
            "reward": {
                "materials": {"sandstone": "7"},
                "xp": {"16": {"sandstone": 13}},
                "money": 180,
                "points": 221,
            }
        },
        "updateblock": {},
    }

    start_resp = {
        "status": "ok",
        "datablock": 1,
        "updateblock": {},
    }

    idle_init = {
        "status": "ok",
        "datablock": {
            "data": {
                "id": "16",
                "stock": {"sandstone": 9, "granite": 10},
                "workers": {
                    "16": {
                        "id": "16",
                        "name": "Skyler",
                        "energy": "80",
                        "productionid": None,  # Idle now
                        "data": {"sandstone": {"level": 3, "points": 35}},
                    }
                },
                "items": {
                    "10": {"id": "10", "name": "shoes", "durability": "18", "stock": "1"},
                    "11": {"id": "11", "name": "pickaxe", "durability": "18", "stock": "1"},
                },
                "productions": {},
            },
            "config": ready_init["datablock"]["config"],
        },
    }

    # Simulate sequence: init_remote -> finish_production -> init_remote -> start_mining -> init_remote
    dummy_client.api_call = AsyncMock(
        side_effect=[
            ready_init,
            finish_resp,
            idle_init,
            start_resp,
            idle_init,
        ]
    )

    service = CraftingVillageService(client=dummy_client)
    res = await service.serve()

    assert res["status"] == "ok"
    assert res["harvested"] == 1
    assert res["started"] == 1


@pytest.mark.asyncio
async def test_refill_worker_energy(dummy_client: MFFGameClient):
    """Verify worker energy refilling using valid energy crops (e.g. Carrots PID 1)."""
    low_energy_init = {
        "status": "ok",
        "datablock": {
            "data": {
                "id": "16",
                "stock": {"sandstone": 5, "granite": 0},
                "workers": {
                    "16": {
                        "id": "16",
                        "name": "Skyler",
                        "energy": "45",
                        "productionid": None,
                        "data": {"sandstone": {"level": 4, "points": 48}},
                    }
                },
                "items": {},
                "productions": {},
                "validenergyproducts": {"1": 1, "2": 2},
            },
            "config": {
                "constants": {"worker_energy": 100},
                "items": {},
                "material": {},
            },
        },
    }

    refill_resp = {
        "status": "ok",
        "datablock": 1,
        "updateblock": {
            "mine": {
                "data": {
                    "level": 1,
                    "stock": {"sandstone": 5},
                    "workers": {
                        "16": {
                            "id": "16",
                            "name": "Skyler",
                            "energy": "100",
                            "productionid": None,
                            "data": {"sandstone": {"level": 4, "points": 48}},
                        }
                    },
                    "items": {},
                    "productions": {},
                    "validenergyproducts": {"1": 1, "2": 2},
                },
                "config": {},
            }
        },
    }

    dummy_client.api_call = AsyncMock(side_effect=[low_energy_init, refill_resp])
    service = CraftingVillageService(client=dummy_client)
    await service.init_remote()

    success = await service.refill_worker_energy(worker_id="16")
    assert success is True

    # Check api_call parameters for refill
    assert dummy_client.api_call.call_count == 2
    call_args = dummy_client.api_call.call_args_list[1]
    assert call_args[0][0] == "farm"
    params = call_args[0][1]
    assert params["mode"] == "mine_worker_refill"
    assert params["id"] == 16
    assert params["pid"] == 1
    assert params["amount"] == 55

    # Check updated energy
    worker = service.snapshot.mine.workers["16"]
    assert worker.energy == 100


@pytest.mark.asyncio
async def test_refill_action_endpoint(
    dummy_client: MFFGameClient,
    auth_headers: dict[str, str],
):
    """Test POST /api/v1/crafting/action/refill endpoint."""
    service = CraftingVillageService(client=dummy_client)
    service.refill_worker_energy = AsyncMock(return_value=True)
    worker_scheduler.last_crafting_village_service = service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.post(
            "/api/v1/crafting/action/refill",
            headers=auth_headers,
            json={"worker_id": "16", "pid": 1, "amount": 55},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["worker_id"] == "16"
        service.refill_worker_energy.assert_awaited_once_with(
            worker_id="16", pid=1, amount=55, stock_service=worker_scheduler.last_stock_service
        )
