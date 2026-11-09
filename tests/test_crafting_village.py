from typing import Any
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

    init_call_count = 0

    async def mock_api(action: str, params: dict[str, Any]) -> dict[str, Any]:
        nonlocal init_call_count
        mode = params.get("mode")
        if mode == "mine_init":
            init_call_count += 1
            return ready_init if init_call_count == 1 else idle_init
        if mode == "stonemason_init":
            return {"status": "ok", "datablock": {}}
        if mode == "mine_finish_production":
            return finish_resp
        if mode == "mine_start_production":
            # Worker 16 becomes busy on the started slot
            idle_init["datablock"]["data"]["workers"]["16"]["productionid"] = "9999"
            return start_resp
        return {"status": "ok"}

    dummy_client.api_call = AsyncMock(side_effect=mock_api)

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

    async def mock_refill_api(action: str, params: dict[str, Any]) -> dict[str, Any]:
        mode = params.get("mode")
        if mode == "mine_init":
            return low_energy_init
        if mode == "stonemason_init":
            return {"status": "ok", "datablock": {}}
        if mode == "mine_worker_refill":
            return refill_resp
        return {"status": "ok"}

    dummy_client.api_call = AsyncMock(side_effect=mock_refill_api)
    service = CraftingVillageService(client=dummy_client)
    await service.init_remote()

    success = await service.refill_worker_energy(worker_id="16")
    assert success is True
    call_args = dummy_client.api_call.call_args_list[-1]
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


@pytest.mark.asyncio
async def test_buy_shop_item_and_coin_protection(dummy_client: MFFGameClient):
    """Verify tool purchasing with coin protection and highest XP selection."""
    mock_init = {
        "status": "ok",
        "datablock": {
            "data": {
                "id": "16",
                "stock": {"sandstone": 5},
                "workers": {},
                "items": {
                    "1": {
                        "id": "1",
                        "name": "shoes",
                        "durability": "10",
                        "stock": "0",
                    }  # in use, 0 available!
                },
                "productions": {},
                "validenergyproducts": {},
            },
            "config": {
                "items": {
                    "walkietalkie": {
                        "name": "Funkgerät",
                        "coins": 25,
                        "money": 0,
                        "durability": 20,
                        "bonus": {"xp": 1.6},
                    },
                    "shoes": {
                        "name": "Stahlkappenschuhe",
                        "coins": 0,
                        "money": 5000,
                        "durability": 20,
                        "bonus": {"xp": 0.8},
                    },
                    "pickaxe": {
                        "name": "Spitzhacke",
                        "coins": 0,
                        "money": 4000,
                        "durability": 20,
                        "bonus": {"xp": 0.4},
                    },
                },
                "material": {},
            },
        },
    }

    dummy_client.api_call = AsyncMock(return_value=mock_init)
    service = CraftingVillageService(client=dummy_client)
    await service.init_remote()

    # 1. Check best buyable tool: Must be 'shoes' (highest XP among 0-coin items)
    best_buyable = service.get_best_buyable_tool()
    assert best_buyable is not None
    tool_name, tool_cfg = best_buyable
    assert tool_name == "shoes"
    assert tool_cfg.coins == 0
    assert tool_cfg.bonus["xp"] == 0.8

    # 2. Strict Coin Protection test: Attempting to buy a coin item must fail!
    coin_buy_result = await service.buy_shop_item(name="walkietalkie", amount=1)
    assert coin_buy_result is False

    # 3. Successful buy of 'shoes'
    buy_resp = {
        "status": "ok",
        "datablock": 1,
        "updateblock": {
            "mine": {
                "data": {
                    "level": 1,
                    "stock": {"sandstone": 5},
                    "workers": {},
                    "items": {
                        "1": {"id": "1", "name": "shoes", "durability": "10", "stock": "0"},
                        "2": {"id": "2", "name": "shoes", "durability": "20", "stock": "1"},
                    },
                    "productions": {},
                },
                "config": mock_init["datablock"]["config"],
            }
        },
    }
    dummy_client.api_call = AsyncMock(return_value=buy_resp)

    buy_success = await service.buy_shop_item(name="shoes", amount=1)
    assert buy_success is True

    # Verify api_call params
    call_args = dummy_client.api_call.call_args_list[0]
    assert call_args[0][0] == "farm"
    assert call_args[0][1]["mode"] == "mine_buy_shop_item"
    assert call_args[0][1]["name"] == "shoes"
    assert call_args[0][1]["amount"] == 1


@pytest.mark.asyncio
async def test_buy_tool_action_endpoint(
    dummy_client: MFFGameClient,
    auth_headers: dict[str, str],
):
    """Test POST /api/v1/crafting/action/buy_tool endpoint."""
    service = CraftingVillageService(client=dummy_client)
    service.buy_shop_item = AsyncMock(return_value=True)
    worker_scheduler.last_crafting_village_service = service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.post(
            "/api/v1/crafting/action/buy_tool",
            headers=auth_headers,
            json={"name": "shoes", "amount": 1},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["tool"] == "shoes"
        service.buy_shop_item.assert_awaited_once_with(
            name="shoes", amount=1, stock_service=worker_scheduler.last_stock_service
        )

@pytest.mark.asyncio
async def test_parse_mine_data_with_new_worker_null_data(dummy_client: MFFGameClient):
    """Test that a newly hired worker with data=None, productions=None, etc. parses correctly."""
    service = CraftingVillageService(client=dummy_client)
    mock_data = {
        "level": 1,
        "workers": {
            "402": {
                "id": "402",
                "name": "Charlie",
                "position": "2",
                "energy": "100",
                "data": None,
                "productionid": "0",
                "createdate": "1793036440",
            }
        },
        "items": None,
        "productions": None,
        "stock": None,
        "validenergyproducts": None,
        "stats": None,
    }
    mock_config = {
        "material": None,
        "items": None,
    }
    snap = service._parse_mine_data(mock_data, mock_config)
    assert "402" in snap.workers
    charlie = snap.workers["402"]
    assert charlie.name == "Charlie"
    assert charlie.energy == 100
    assert charlie.is_working is False
    assert charlie.data == {}


@pytest.mark.asyncio
async def test_stonemason_models_and_parsing(dummy_client: MFFGameClient):
    """Verify that CraftingVillageService parses stonemason_init data properly."""
    mock_sm_init = {'datablock': {'data': {'id': '104', 'unr': '17626250', 'points': '0', 'count': '22', 'count_alltime': '22', 'count_alltime_lastchange': '1794242736', 'count_buy': None, 'tools': {'1': 11}, 'tutorial': {'start': 1794196626}, 'createdate': '1794196605', 'level': 1, 'customers': {'5448': {'id': '5448', 'unr': '17626250', 'position': '3', 'name': 'Elia', 'pid': '1', 'img': {'color': 1, 'body': 7, 'face': 2, 'hair': 7, 'eyes': 6, 'mouth': 5, 'nose': 4}, 'slot': '0', 'grid': None, 'reward_multi': {'money': 1.19, 'points': 1.07, 'stonemason_points': 1.2}, 'reward': '', 'createdate': '1794196005', 'finishdate': '0', 'remain': -46378}, '5449': {'id': '5449', 'unr': '17626250', 'position': '4', 'name': 'Maxi', 'pid': '1', 'img': {'color': 3, 'body': 8, 'face': 4, 'hair': 10, 'eyes': 3, 'mouth': 7, 'nose': 1}, 'slot': '0', 'grid': None, 'reward_multi': {'money': 0.85, 'points': 0.9, 'stonemason_points': 1.11}, 'reward': '', 'createdate': '1794196005', 'finishdate': '0', 'remain': -46378}, '5597': {'id': '5597', 'unr': '17626250', 'position': '1', 'name': 'Kennedy', 'pid': '1', 'img': {'color': 4, 'body': 2, 'face': 4, 'hair': 6, 'eyes': 2, 'mouth': 2, 'nose': 1}, 'slot': '0', 'grid': None, 'reward_multi': {'money': 1.05, 'points': 0.97, 'stonemason_points': 1.19}, 'reward': '', 'createdate': '1794239103', 'finishdate': '0', 'remain': -3280}, '5613': {'id': '5613', 'unr': '17626250', 'position': '2', 'name': 'Loris', 'pid': '1', 'img': {'color': 2, 'body': 2, 'face': 2, 'hair': 1, 'eyes': 7, 'mouth': 5, 'nose': 5}, 'slot': '0', 'grid': None, 'reward_multi': {'money': 0.84, 'points': 1.02, 'stonemason_points': 0.84}, 'reward': '', 'createdate': '1794242736', 'finishdate': '0', 'remain': 353}}, 'level_percent': 0, 'level_rest': 100, 'exchangecoins': 2, 'exchangeamount': 25, 'stats': {'buy_tool1': '10', 'buy_tools': '10', 'customer_served': '2', 'customer_served_perfect': '2', 'product1_perfect': '2', 'tiles_removed': '4', 'tool1_used': '4', 'tools_used': '4'}, 'achievements': []}, 'config': {'constants': {'buy_leveladd_coins': 2, 'customerimg': {'color': 4, 'body': 9, 'face': 4, 'hair': 10, 'eyes': 7, 'mouth': 9, 'nose': 5}, 'speedup_tile': 600, 'customer_cooldown': 600, 'customer_speedup': 5000, 'tools_init': {'1': 5}, 'reward_multi': {'min': 0.8, 'max': 1.2}}, 'levels': {'1': {'points': 0, 'points_base': 25}, '2': {'points': 100, 'points_base': 50}, '3': {'points': 750, 'points_base': 200}, '4': {'points': 1500, 'points_base': 300}, '5': {'points': 6000, 'points_base': 500}, '6': {'points': 15000, 'points_base': 2000}, '7': {'points': 27000, 'points_base': 5000}, '8': {'points': 53000, 'points_base': 10000}, '9': {'points': 84000, 'points_base': 20000}, '10': {'points': 188000, 'points_base': 20000}, '11': {'points': 300000, 'points_base': 20000}, '12': {'points': 556000, 'points_base': 20000}, '13': {'points': 853000, 'points_base': 20000}, '14': {'points': 1555000, 'points_base': 20000}, '15': {'points': 2338000, 'points_base': 20000}, '16': {'points': 4210000, 'points_base': 20000}, '17': {'points': 6082000, 'points_base': 20000}, '18': {'points': 9457000, 'points_base': 20000}, '19': {'points': 13282000, 'points_base': 20000}, '20': {'points': 18382000, 'points_base': 20000}}, 'slots': {'1': {'level': 1}, '2': {'level': 5}, '3': {'level': 10}, '4': {'level': 15}}, 'customers': {'1': [], '2': [], '3': [], '4': []}, 'tools': {'1': {'name': 'Meißel', 'x': 1, 'y': 1, 'duration': 1800, 'money': {'100': 1, '200': 2, '500': 10}}, '2': {'name': 'Langer Meißel', 'x': 2, 'y': 1, 'duration': 2700, 'money': {'200': 1, '400': 2, '1000': 10}}, '3': {'name': 'Dünner Meißel', 'x': 1, 'y': 2, 'duration': 2700, 'money': {'200': 1, '400': 2, '1000': 10}}, '4': {'name': 'Großer Meißel', 'x': 2, 'y': 2, 'duration': 3200, 'money': {'300': 1, '600': 2, '1500': 10}}, '5': {'name': 'Riesenmeißel', 'x': 3, 'y': 3, 'duration': 1800, 'coins': {'2': 1, '4': 2, '20': 10}}, '6': {'name': 'Kreuzmeißel', 'form': '+', 'duration': 1800, 'coins': {'1': 1, '2': 2, '10': 10}}, '7': {'name': 'Rundmeißel', 'form': 'o', 'duration': 1800, 'coins': {'4': 1, '8': 2, '40': 10}}}, 'products': {'1': {'name': 'Sandstein-Ziegel', 'material': {'sandstone': 3}, 'level': 1, 'x': 4, 'y': 4, 'allowed': [1, 16], 'reward': {'money': 714, 'points': 833, 'stonemason_points': 10}}, '2': {'name': 'Granitziegel', 'material': {'granite': 5}, 'level': 2, 'x': 5, 'y': 5, 'allowed': [1, 2, 6, 20, 21, 24, 25], 'reward': {'money': 2778, 'points': 794, 'stonemason_points': 25}}, '3': {'name': 'Ziegelmauerstein', 'material': {'sandstone': 10}, 'level': 3, 'x': 6, 'y': 6, 'allowed': [1, 2, 3, 6, 7, 8, 24, 29, 30, 31, 34, 35, 36], 'reward': {'money': 3174, 'points': 3968, 'stonemason_points': 50}}, '4': {'name': 'Granitpflasterstein', 'material': {'granite': 10}, 'level': 4, 'x': 6, 'y': 6, 'allowed': [1, 2, 3, 5, 6, 7, 8, 23, 24, 25, 28, 29, 30, 31, 34, 35, 36], 'reward': {'money': 9243, 'points': 2521, 'stonemason_points': 100}}, '5': {'name': 'Ziegelskulptur', 'material': {'sandstone': 15}, 'level': 5, 'x': 7, 'y': 7, 'allowed': [1, 2, 3, 4, 7, 8, 9, 10, 27, 28, 29, 30, 34, 35, 36, 42, 43, 44, 48, 49], 'reward': {'money': 5952, 'points': 9523, 'stonemason_points': 200}}, '6': {'name': 'Granitstatue', 'material': {'granite': 15}, 'level': 6, 'x': 7, 'y': 7, 'allowed': [1, 2, 3, 6, 7, 8, 9, 13, 14, 15, 16, 20, 21, 22, 28, 29, 30, 34, 35, 36, 37, 41, 42, 43, 44, 48, 49], 'reward': {'money': 15476, 'points': 4167, 'stonemason_points': 400}}, '7': {'name': 'Ziegelofen', 'material': {'sandstone': 20}, 'level': 8, 'x': 8, 'y': 8, 'allowed': [1, 2, 7, 8, 9, 10, 15, 16, 17, 24, 33, 41, 44, 45, 48, 49, 52, 53, 56, 57, 64], 'reward': {'money': 11111, 'points': 15873, 'stonemason_points': 800}}, '8': {'name': 'Granitbrunnen', 'material': {'granite': 20}, 'level': 10, 'x': 8, 'y': 8, 'allowed': [1, 2, 3, 6, 7, 8, 9, 16, 17, 18, 23, 24, 25, 32, 33, 34, 39, 40, 41, 42, 43, 46, 47, 48, 49, 50, 55, 56, 57, 58, 63, 64], 'reward': {'money': 23809, 'points': 6349, 'stonemason_points': 1500}}, '9': {'name': 'Ziegelbogen', 'material': {'sandstone': 35}, 'level': 12, 'x': 9, 'y': 9, 'allowed': [1, 2, 3, 4, 7, 8, 9, 10, 11, 12, 19, 20, 28, 37, 43, 45, 46, 52, 53, 54, 61, 62, 63, 64, 69, 70, 71, 72, 73, 74, 75, 77, 78, 79, 80, 81], 'reward': {'money': 22857, 'points': 34285, 'stonemason_points': 3000}}, '10': {'name': 'Granit-Säule', 'material': {'granite': 40}, 'level': 14, 'x': 9, 'y': 9, 'allowed': [1, 2, 3, 7, 8, 9, 10, 11, 12, 16, 17, 18, 19, 20, 21, 22, 24, 25, 26, 27, 28, 29, 30, 31, 33, 34, 35, 36, 37, 38, 39, 40, 42, 43, 44, 45, 46, 47, 48, 52, 53, 54, 55, 56, 57, 62, 63, 64, 65, 66, 71, 72, 73, 74, 75, 76, 79, 80, 81], 'reward': {'money': 60714, 'points': 16071, 'stonemason_points': 6000}}, '11': {'name': 'Grabstein', 'material': {'sandstone': 45}, 'level': 16, 'x': 9, 'y': 9, 'allowed': [1, 2, 3, 7, 8, 9, 10, 11, 12, 17, 18, 19, 20, 26, 27, 28, 29, 35, 36, 37, 38, 44, 45, 46, 47, 53, 54, 55, 56, 62, 63, 64, 65, 71, 72, 73, 81], 'reward': {'money': 35714, 'points': 48214, 'stonemason_points': 10000}}, '12': {'name': 'Granitdenkmal', 'material': {'granite': 45}, 'level': 18, 'x': 10, 'y': 10, 'allowed': [1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 17, 18, 19, 20, 21, 22, 23, 24, 28, 29, 30, 31, 32, 33, 34, 38, 39, 40, 41, 42, 43, 48, 49, 50, 51, 52, 59, 60, 61, 62, 63, 68, 69, 70, 71, 72, 73, 79, 80, 81, 82, 83, 89, 90, 91, 92, 93, 94, 98, 99, 100], 'reward': {'money': 71428, 'points': 17857, 'stonemason_points': 15000}}}, 'stars': {'1': {'points': 5, 'percent': 0.025}, '2': {'points': 25, 'percent': 0.05}, '3': {'points': 100, 'percent': 0.075}, '4': {'points': 250, 'percent': 0.1}, '5': {'points': 1000, 'percent': 0.2}}, 'achievements': {'customer_served': {'10': 1744, '100': 1745, '1000': 1746}, 'customer_served_perfect': {'10': 1747, '100': 1748}}}}, 'updateblock': {'stock': {'stock': {'1': {'1': {'1': {'pid': '1', 'amount': '4589', 'points': 3, 'duration': 1200}, '2': {'pid': '2', 'amount': '83', 'points': 17, 'duration': 2700}, '3': {'pid': '3', 'amount': '12113', 'points': 10, 'duration': 2700}, '4': {'pid': '4', 'amount': '609', 'points': 44, 'duration': 5400}, '5': {'pid': '5', 'amount': '1148', 'points': 64, 'duration': 7200}, '6': {'pid': '6', 'amount': '200', 'points': 128, 'duration': 14400}, '7': {'pid': '7', 'amount': '500', 'points': 300, 'bonus': 1200, 'duration': 27600}, '8': {'pid': '8', 'amount': '1372', 'points': 600, 'bonus': 1800, 'duration': 55800}, '9': {'pid': '9', 'amount': '52835', 'points': 750, 'duration': 14400}, '10': {'pid': '10', 'amount': '28665', 'points': 950, 'duration': 43200}, '11': {'pid': '11', 'amount': '33590', 'points': 1540, 'duration': 86400}, '12': {'pid': '12', 'amount': '18175', 'points': 2350, 'duration': 172800}, '13': {'pid': '17', 'amount': '200', 'points': 1, 'duration': 810, 'oldtime': 900}, '14': {'pid': '18', 'amount': '350', 'points': 7, 'duration': 5400}, '15': {'pid': '19', 'amount': '16757', 'points': 24, 'bonus': 600, 'duration': 13800}, '16': {'pid': '20', 'amount': '455', 'points': 42, 'bonus': 1200, 'duration': 27600}, '17': {'pid': '21', 'amount': '15', 'points': 63, 'bonus': 1200, 'duration': 34800}, '18': {'pid': '22', 'amount': '476', 'points': 52, 'bonus': 1200, 'duration': 28800}, '19': {'pid': '23', 'amount': '20136', 'points': 88, 'bonus': 1800, 'duration': 46200}, '20': {'pid': '24', 'amount': '31353', 'points': 92, 'bonus': 1800, 'duration': 41400}}, '2': {'1': {'pid': '25', 'amount': '44804', 'points': 3100, 'need': [{'pid': 9, 'amount': 30}], 'duration': 60000}, '2': {'pid': '26', 'amount': '2591', 'points': 108, 'bonus': 1800, 'duration': 45000}, '3': {'pid': '27', 'amount': '5217', 'points': 4500, 'need': [{'pid': 10, 'amount': 12}], 'duration': 120000}, '4': {'pid': '28', 'amount': '261', 'points': 5000, 'need': [{'pid': 11, 'amount': 8}], 'duration': 180000}, '5': {'pid': '29', 'amount': '1265', 'points': 319, 'bonus': 1800, 'duration': 55200}, '6': {'pid': '30', 'amount': '655', 'points': 5400, 'need': [{'pid': 12, 'amount': 5}], 'duration': 240000}, '7': {'pid': '31', 'amount': '162', 'points': 179, 'bonus': 1800, 'duration': 58200}, '8': {'pid': '32', 'amount': '1062', 'points': 133, 'bonus': 1200, 'duration': 42000}, '9': {'pid': '33', 'amount': '371', 'points': 229, 'bonus': 3600, 'duration': 68400}, '10': {'pid': '34', 'amount': '484', 'points': 157, 'bonus': 1800, 'duration': 46200}, '11': {'pid': '35', 'amount': '251', 'points': 405, 'bonus': 2400, 'duration': 117600}, '12': {'pid': '36', 'amount': '377', 'points': 733, 'bonus': 1800, 'duration': 51000}, '13': {'pid': '37', 'amount': '63', 'points': 2569, 'bonus': 3600, 'duration': 176400}, '14': {'pid': '38', 'amount': '2520', 'points': 211, 'bonus': 1800, 'duration': 55800}, '15': {'pid': '39', 'amount': '500', 'points': 3611, 'bonus': 3600, 'duration': 236400}, '16': {'pid': '40', 'amount': '323', 'points': 4444, 'bonus': 3600, 'duration': 284400}, '17': {'pid': '41', 'amount': '74', 'points': 5220, 'bonus': 3600, 'duration': 326400}, '18': {'pid': '42', 'amount': '327', 'points': 6028, 'bonus': 3600, 'duration': 368400}, '19': {'pid': '43', 'amount': '222', 'points': 6769, 'bonus': 3600, 'duration': 404400}, '20': {'pid': '44', 'amount': '1099', 'points': 1833, 'bonus': 2400, 'duration': 429600}}, '3': {'1': {'pid': '91', 'amount': '20', 'points': 2500, 'duration': 172800}, '2': {'pid': '92', 'amount': '128', 'points': 3000, 'duration': 32400}, '3': {'pid': '93', 'amount': '8', 'points': 3000, 'duration': 32400}, '4': {'pid': '107', 'amount': '10', 'points': 0, 'duration': 42000, 'block': 1}, '5': {'pid': '108', 'amount': '4304', 'points': 179, 'duration': 21600}, '6': {'pid': '109', 'amount': '1360', 'points': 31, 'duration': 8100}, '7': {'pid': '110', 'amount': '34304', 'points': 2300, 'duration': 129600}, '8': {'pid': '111', 'amount': '5166', 'points': 4900, 'need': [{'pid': 110, 'amount': 5}], 'duration': 59040}, '9': {'pid': '112', 'amount': '659', 'points': 490, 'duration': 86400}, '10': {'pid': '113', 'amount': '34807', 'points': 340, 'duration': 100800}, '11': {'pid': '114', 'amount': '355', 'points': 240, 'duration': 32400}, '12': {'pid': '115', 'amount': '232', 'points': 1174, 'duration': 306000}, '13': {'pid': '126', 'amount': '467', 'points': 1475, 'duration': 384000}, '14': {'pid': '127', 'amount': '5', 'points': 6540, 'duration': 444000}, '15': {'pid': '128', 'amount': '164', 'points': 7955, 'duration': 468000}, '16': {'pid': '129', 'amount': '39', 'points': 0, 'duration': 38400, 'block': 1}, '17': {'pid': '144', 'amount': '1367', 'points': 3600, 'need': [{'pid': 21, 'amount': 450}], 'duration': 22500}, '18': {'pid': '151', 'amount': '1026', 'points': 5400, 'duration': 187200}, '19': {'pid': '152', 'amount': '350', 'points': 7750, 'need': [{'pid': 151, 'amount': 5}], 'duration': 194400}, '20': {'pid': '153', 'amount': '1000', 'points': 450, 'duration': 86400}}, '12': {'1': {'pid': '116', 'amount': '12109', 'points': 890, 'need': [{'pid': 2, 'amount': 1980}], 'duration': 50400}, '2': {'pid': '117', 'amount': '8568', 'points': 1240, 'need': [{'pid': 7, 'amount': 550}], 'duration': 64800}, '3': {'pid': '118', 'amount': '15715', 'points': 580, 'need': [{'pid': 4, 'amount': 1020}], 'duration': 28800}, '4': {'pid': '119', 'amount': '9061', 'points': 1640, 'need': [{'pid': 38, 'amount': 650}], 'duration': 79200}, '5': {'pid': '120', 'amount': '9299', 'points': 2320, 'need': [{'pid': 42, 'amount': 110}], 'duration': 108000}, '6': {'pid': '121', 'amount': '10032', 'points': 3400, 'need': [{'pid': 43, 'amount': 100}], 'duration': 151200}, '7': {'pid': '122', 'amount': '8', 'points': 2000, 'need': [{'pid': 112, 'amount': 130}, {'pid': 116, 'amount': 35}], 'duration': 75600}, '8': {'pid': '123', 'amount': '781', 'points': 2700, 'need': [{'pid': 113, 'amount': 220}, {'pid': 117, 'amount': 35}], 'duration': 97200}, '9': {'pid': '124', 'amount': '10', 'points': 1250, 'need': [{'pid': 114, 'amount': 345}, {'pid': 118, 'amount': 35}], 'duration': 43200}, '10': {'pid': '125', 'amount': '1259', 'points': 7200, 'need': [{'pid': 115, 'amount': 80}, {'pid': 119, 'amount': 35}], 'duration': 226800}}, '30': {'1': {'pid': '130', 'amount': '47', 'points': 36, 'need': [{'pid': 17, 'amount': 400}], 'duration': 10800}, '2': {'pid': '131', 'amount': '48', 'points': 36, 'need': [{'pid': 21, 'amount': 150}], 'duration': 10800}, '3': {'pid': '132', 'amount': '51', 'points': 18, 'need': [{'pid': 20, 'amount': 100}, {'pid': 10, 'amount': 2}], 'duration': 5400}, '4': {'pid': '133', 'amount': '51', 'points': 18, 'need': [{'pid': 19, 'amount': 100}, {'pid': 10, 'amount': 2}], 'duration': 5400}, '5': {'pid': '134', 'amount': '45', 'points': 40, 'need': [{'pid': 17, 'amount': 400}, {'pid': 6, 'amount': 25}], 'duration': 12000}, '6': {'pid': '135', 'amount': '45', 'points': 36, 'need': [{'pid': 21, 'amount': 150}, {'pid': 6, 'amount': 25}], 'duration': 10800}, '7': {'pid': '136', 'amount': '53', 'points': 20, 'need': [{'pid': 2, 'amount': 120}], 'duration': 6000}, '8': {'pid': '137', 'amount': '52', 'points': 26, 'need': [{'pid': 1, 'amount': 200}], 'duration': 7800}, '9': {'pid': '138', 'amount': '52', 'points': 30, 'need': [{'pid': 1, 'amount': 200}, {'pid': 23, 'amount': 30}], 'duration': 9000}, '10': {'pid': '139', 'amount': '51', 'points': 16, 'need': [{'pid': 18, 'amount': 150}, {'pid': 22, 'amount': 30}], 'duration': 4800}, '11': {'pid': '140', 'amount': '45', 'points': 16, 'need': [{'pid': 21, 'amount': 150}, {'pid': 22, 'amount': 30}], 'duration': 4800}, '12': {'pid': '141', 'amount': '52', 'points': 24, 'need': [{'pid': 26, 'amount': 100}, {'pid': 144, 'amount': 3}], 'duration': 7200}, '13': {'pid': '142', 'amount': '51', 'points': 24, 'need': [{'pid': 26, 'amount': 100}, {'pid': 25, 'amount': 3}], 'duration': 7200}, '14': {'pid': '143', 'amount': '50', 'points': 24, 'need': [{'pid': 26, 'amount': 100}, {'pid': 144, 'amount': 3}, {'pid': 25, 'amount': 3}], 'duration': 7200}, '15': {'pid': '145', 'amount': '55', 'points': 60, 'need': [{'pid': 33, 'amount': 300}, {'pid': 12, 'amount': 2}], 'duration': 18000}, '16': {'pid': '146', 'amount': '50', 'points': 48, 'need': [{'pid': 35, 'amount': 200}, {'pid': 10, 'amount': 4}, {'pid': 12, 'amount': 2}], 'duration': 14400}, '17': {'pid': '147', 'amount': '53', 'points': 60, 'need': [{'pid': 37, 'amount': 60}, {'pid': 12, 'amount': 2}], 'duration': 18000}, '18': {'pid': '148', 'amount': '50', 'points': 69, 'need': [{'pid': 31, 'amount': 250}, {'pid': 26, 'amount': 150}, {'pid': 27, 'amount': 7}], 'duration': 28800}, '19': {'pid': '149', 'amount': '45', 'points': 44, 'need': [{'pid': 21, 'amount': 300}, {'pid': 43, 'amount': 10}, {'pid': 22, 'amount': 50}, {'pid': 121, 'amount': 4}], 'duration': 13200}, '20': {'pid': '150', 'amount': '50', 'points': 72, 'need': [{'pid': 38, 'amount': 60}, {'pid': 119, 'amount': 6}, {'pid': 17, 'amount': 50}], 'duration': 21600}}, '4': {'1': {'pid': '154', 'amount': '26', 'points': 2600, 'duration': 115200}, '2': {'pid': '155', 'amount': '1670', 'points': 8250, 'need': [{'pid': 152, 'amount': 5}, {'pid': 28, 'amount': 6}], 'duration': 86400}, '3': {'pid': '156', 'amount': '760', 'points': 9725, 'need': [{'pid': 152, 'amount': 8}, {'pid': 28, 'amount': 5}], 'duration': 151200}, '4': {'pid': '157', 'amount': '5595', 'points': 6250, 'need': [{'pid': 152, 'amount': 2}, {'pid': 28, 'amount': 9}], 'duration': 50400}, '5': {'pid': '159', 'amount': '1150', 'points': 4900, 'duration': 28800}, '6': {'pid': '160', 'amount': '10', 'points': 5200, 'duration': 28800}, '7': {'pid': '350', 'amount': '94', 'points': 2500, 'duration': 21600}, '8': {'pid': '351', 'amount': '709', 'points': 210, 'duration': 28800}, '9': {'pid': '352', 'amount': '659', 'points': 125, 'duration': 21600}, '10': {'pid': '353', 'amount': '639', 'points': 632, 'duration': 38880, 'oldtime': 43200}, '11': {'pid': '354', 'amount': '619', 'points': 634, 'duration': 43200}, '12': {'pid': '355', 'amount': '719', 'points': 265, 'duration': 79200}, '13': {'pid': '356', 'amount': '758', 'points': 2533, 'duration': 172800}, '14': {'pid': '357', 'amount': '670', 'points': 105, 'duration': 28800}, '15': {'pid': '358', 'amount': '738', 'points': 1270, 'duration': 86400}, '16': {'pid': '359', 'amount': '614', 'points': 425, 'duration': 28800}, '17': {'pid': '360', 'amount': '647', 'points': 635, 'duration': 86400}, '18': {'pid': '361', 'amount': '567', 'points': 162, 'duration': 10800}, '19': {'pid': '700', 'amount': '4481', 'points': 635, 'duration': 43200}, '20': {'pid': '701', 'amount': '4332', 'points': 80, 'duration': 21600}}, '31': {'1': {'pid': '161', 'amount': '50', 'points': 41, 'need': [{'pid': 1, 'amount': 300}, {'pid': 17, 'amount': 150}], 'duration': 12600}, '2': {'pid': '162', 'amount': '52', 'points': 30, 'need': [{'pid': 1, 'amount': 250}, {'pid': 20, 'amount': 200}], 'duration': 7800}, '3': {'pid': '163', 'amount': '53', 'points': 45, 'need': [{'pid': 22, 'amount': 190}, {'pid': 26, 'amount': 120}], 'duration': 15600}, '4': {'pid': '164', 'amount': '52', 'points': 35, 'need': [{'pid': 1, 'amount': 150}, {'pid': 10, 'amount': 30}], 'duration': 11400}, '5': {'pid': '165', 'amount': '52', 'points': 60, 'need': [{'pid': 12, 'amount': 5}, {'pid': 10, 'amount': 10}], 'duration': 9600}, '6': {'pid': '166', 'amount': '53', 'points': 42, 'need': [{'pid': 27, 'amount': 7}, {'pid': 10, 'amount': 10}], 'duration': 11700}, '7': {'pid': '167', 'amount': '522', 'points': 36, 'need': [{'pid': 40, 'amount': 60}, {'pid': 111, 'amount': 10}], 'duration': 15000}, '8': {'pid': '168', 'amount': '52', 'points': 23, 'need': [{'pid': 42, 'amount': 25}, {'pid': 10, 'amount': 10}], 'duration': 5400}, '9': {'pid': '169', 'amount': '36', 'points': 17, 'need': [{'pid': 6, 'amount': 300}, {'pid': 9, 'amount': 15}, {'pid': 121, 'amount': 3}], 'duration': 9000}, '10': {'pid': '170', 'amount': '58', 'points': 23, 'need': [{'pid': 1, 'amount': 200}, {'pid': 121, 'amount': 4}, {'pid': 43, 'amount': 20}], 'duration': 18000}, '11': {'pid': '450', 'amount': '52', 'points': 44, 'need': [{'pid': 20, 'amount': 190}, {'pid': 10, 'amount': 15}], 'duration': 7920}, '12': {'pid': '451', 'amount': '145', 'points': 16, 'need': [{'pid': 33, 'amount': 120}, {'pid': 111, 'amount': 5}], 'duration': 3360}, '13': {'pid': '452', 'amount': '52', 'points': 36, 'need': [{'pid': 34, 'amount': 100}, {'pid': 9, 'amount': 15}], 'duration': 10080}, '14': {'pid': '453', 'amount': '50', 'points': 34, 'need': [{'pid': 35, 'amount': 150}, {'pid': 10, 'amount': 10}], 'duration': 7920}, '15': {'pid': '454', 'amount': '52', 'points': 33, 'need': [{'pid': 36, 'amount': 190}, {'pid': 10, 'amount': 12}], 'duration': 11520}, '16': {'pid': '455', 'amount': '483', 'points': 35, 'need': [{'pid': 37, 'amount': 200}, {'pid': 9, 'amount': 8}], 'duration': 8640}, '17': {'pid': '456', 'amount': '51', 'points': 30, 'need': [{'pid': 40, 'amount': 40}, {'pid': 10, 'amount': 8}], 'duration': 6960}, '18': {'pid': '457', 'amount': '51', 'points': 55, 'need': [{'pid': 351, 'amount': 20}, {'pid': 9, 'amount': 8}], 'duration': 7200}, '19': {'pid': '458', 'amount': '53', 'points': 50, 'need': [{'pid': 356, 'amount': 10}, {'pid': 10, 'amount': 12}], 'duration': 8640}, '20': {'pid': '459', 'amount': '51', 'points': 55, 'need': [{'pid': 361, 'amount': 18}, {'pid': 111, 'amount': 8}], 'duration': 7920}}, '40': {'1': {'pid': '171', 'amount': '6360', 'points': 252, 'duration': 25200}, '2': {'pid': '172', 'amount': '6043', 'points': 165, 'duration': 16200}, '3': {'pid': '173', 'amount': '6390', 'points': 310, 'duration': 32400}, '4': {'pid': '174', 'amount': '6009', 'points': 137, 'duration': 13200}, '5': {'pid': '175', 'amount': '28085', 'points': 114, 'duration': 10800}, '6': {'pid': '176', 'amount': '28902', 'points': 207, 'duration': 18900}, '7': {'pid': '177', 'amount': '28776', 'points': 258, 'duration': 23400}, '8': {'pid': '178', 'amount': '20460', 'points': 173, 'duration': 15000}, '9': {'pid': '179', 'amount': '22949', 'points': 161, 'duration': 13800}, '10': {'pid': '180', 'amount': '21010', 'points': 257, 'duration': 20700}, '11': {'pid': '181', 'amount': '6863', 'points': 420, 'duration': 36000}, '12': {'pid': '182', 'amount': '28381', 'points': 192, 'duration': 14700}, '13': {'pid': '183', 'amount': '5965', 'points': 701, 'duration': 52800}, '14': {'pid': '184', 'amount': '6094', 'points': 290, 'duration': 21600}, '15': {'pid': '185', 'amount': '6067', 'points': 425, 'duration': 30600}, '16': {'pid': '186', 'amount': '6083', 'points': 380, 'duration': 26400}, '17': {'pid': '187', 'amount': '15900', 'points': 220, 'duration': 14400}, '18': {'pid': '188', 'amount': '5', 'points': 0, 'duration': 0, 'block': 1}, '19': {'pid': '189', 'amount': '2', 'points': 0, 'duration': 0, 'block': 1}, '20': {'pid': '200', 'amount': '2', 'points': 520, 'need': [{'pid': 171, 'amount': 24}, {'pid': 172, 'amount': 20}, {'pid': 173, 'amount': 12}], 'duration': 14400}}, '41': {'1': {'pid': '204', 'amount': '1', 'points': 810, 'need': [{'pid': 176, 'amount': 21}, {'pid': 173, 'amount': 18}, {'pid': 171, 'amount': 7}, {'pid': 174, 'amount': 8}], 'duration': 21000}, '2': {'pid': '206', 'amount': '12', 'points': 515, 'need': [{'pid': 178, 'amount': 9}, {'pid': 176, 'amount': 9}, {'pid': 174, 'amount': 16}, {'pid': 177, 'amount': 10}], 'duration': 10800}, '3': {'pid': '207', 'amount': '2', 'points': 634, 'need': [{'pid': 173, 'amount': 15}, {'pid': 179, 'amount': 8}, {'pid': 177, 'amount': 11}, {'pid': 175, 'amount': 10}], 'duration': 15000}, '4': {'pid': '208', 'amount': '6', 'points': 660, 'need': [{'pid': 180, 'amount': 18}, {'pid': 178, 'amount': 15}, {'pid': 175, 'amount': 13}, {'pid': 173, 'amount': 12}], 'duration': 17400}, '5': {'pid': '209', 'amount': '9', 'points': 762, 'need': [{'pid': 181, 'amount': 9}, {'pid': 179, 'amount': 7}, {'pid': 176, 'amount': 21}, {'pid': 171, 'amount': 12}], 'duration': 22800}, '6': {'pid': '210', 'amount': '1', 'points': 1343, 'need': [{'pid': 171, 'amount': 12}, {'pid': 182, 'amount': 11}, {'pid': 179, 'amount': 34}, {'pid': 175, 'amount': 5}], 'duration': 10800}, '7': {'pid': '211', 'amount': '6', 'points': 1500, 'need': [{'pid': 183, 'amount': 18}, {'pid': 181, 'amount': 22}, {'pid': 176, 'amount': 12}, {'pid': 172, 'amount': 9}], 'duration': 19500}, '8': {'pid': '212', 'amount': '9', 'points': 361, 'need': [{'pid': 171, 'amount': 13}, {'pid': 182, 'amount': 13}, {'pid': 183, 'amount': 16}, {'pid': 184, 'amount': 6}], 'duration': 7200}, '9': {'pid': '213', 'amount': '5', 'points': 852, 'need': [{'pid': 186, 'amount': 9}, {'pid': 187, 'amount': 7}, {'pid': 185, 'amount': 7}, {'pid': 179, 'amount': 8}, {'pid': 171, 'amount': 3}], 'duration': 12600}, '10': {'pid': '220', 'amount': '67', 'points': 45000, 'need': [{'pid': 188, 'amount': 1}, {'pid': 171, 'amount': 70}, {'pid': 172, 'amount': 180}, {'pid': 173, 'amount': 80}, {'pid': 174, 'amount': 80}], 'duration': 86400, 'block': 1}, '11': {'pid': '221', 'amount': '178', 'points': 45000, 'need': [{'pid': 189, 'amount': 1}, {'pid': 171, 'amount': 80}, {'pid': 172, 'amount': 50}, {'pid': 173, 'amount': 65}, {'pid': 174, 'amount': 140}], 'duration': 86400, 'block': 1}}, '32': {'1': {'pid': '460', 'amount': '54', 'points': 45, 'need': [{'pid': 360, 'amount': 14}, {'pid': 10, 'amount': 15}], 'duration': 9600}, '2': {'pid': '461', 'amount': '52', 'points': 60, 'need': [{'pid': 357, 'amount': 18}, {'pid': 10, 'amount': 16}], 'duration': 10080}, '3': {'pid': '462', 'amount': '52', 'points': 55, 'need': [{'pid': 352, 'amount': 16}, {'pid': 9, 'amount': 20}], 'duration': 12480}, '4': {'pid': '463', 'amount': '52', 'points': 65, 'need': [{'pid': 353, 'amount': 20}, {'pid': 10, 'amount': 18}], 'duration': 10800}, '5': {'pid': '464', 'amount': '53', 'points': 70, 'need': [{'pid': 359, 'amount': 22}, {'pid': 111, 'amount': 14}], 'duration': 7920}, '6': {'pid': '465', 'amount': '1264', 'points': 70, 'need': [{'pid': 355, 'amount': 12}, {'pid': 9, 'amount': 16}], 'duration': 10080}, '7': {'pid': '466', 'amount': '55', 'points': 65, 'need': [{'pid': 354, 'amount': 15}, {'pid': 111, 'amount': 8}, {'pid': 12, 'amount': 5}], 'duration': 11520}, '8': {'pid': '467', 'amount': '53', 'points': 80, 'need': [{'pid': 358, 'amount': 14}, {'pid': 10, 'amount': 12}, {'pid': 9, 'amount': 16}], 'duration': 12480}, '9': {'pid': '468', 'amount': '51', 'points': 65, 'need': [{'pid': 356, 'amount': 20}, {'pid': 10, 'amount': 6}], 'duration': 10560}, '10': {'pid': '469', 'amount': '55', 'points': 50, 'need': [{'pid': 6, 'amount': 40}, {'pid': 37, 'amount': 120}, {'pid': 352, 'amount': 60}], 'duration': 11520}, '11': {'pid': '470', 'amount': '188', 'points': 70, 'need': [{'pid': 359, 'amount': 80}, {'pid': 355, 'amount': 50}, {'pid': 351, 'amount': 120}], 'duration': 10800}, '12': {'pid': '471', 'amount': '51', 'points': 60, 'need': [{'pid': 360, 'amount': 40}, {'pid': 351, 'amount': 100}, {'pid': 10, 'amount': 12}], 'duration': 9120}, '13': {'pid': '472', 'amount': '51', 'points': 55, 'need': [{'pid': 360, 'amount': 100}, {'pid': 10, 'amount': 20}], 'duration': 7920}, '14': {'pid': '473', 'amount': '51', 'points': 75, 'need': [{'pid': 357, 'amount': 120}, {'pid': 352, 'amount': 120}], 'duration': 8640}, '15': {'pid': '474', 'amount': '31', 'points': 50, 'need': [{'pid': 154, 'amount': 50}, {'pid': 6, 'amount': 20}, {'pid': 117, 'amount': 6}], 'duration': 11520}, '16': {'pid': '475', 'amount': '52', 'points': 58, 'need': [{'pid': 44, 'amount': 40}, {'pid': 153, 'amount': 40}, {'pid': 9, 'amount': 20}], 'duration': 9600}, '17': {'pid': '476', 'amount': '50', 'points': 65, 'need': [{'pid': 115, 'amount': 25}, {'pid': 6, 'amount': 15}, {'pid': 111, 'amount': 6}], 'duration': 12240}, '18': {'pid': '477', 'amount': '202', 'points': 72, 'need': [{'pid': 351, 'amount': 80}, {'pid': 27, 'amount': 12}, {'pid': 1, 'amount': 200}], 'duration': 13440}, '19': {'pid': '478', 'amount': '51', 'points': 50, 'need': [{'pid': 39, 'amount': 20}, {'pid': 111, 'amount': 6}], 'duration': 7680}, '20': {'pid': '479', 'amount': '52', 'points': 45, 'need': [{'pid': 41, 'amount': 20}, {'pid': 9, 'amount': 10}, {'pid': 1, 'amount': 250}], 'duration': 9600}}, '33': {'1': {'pid': '480', 'amount': '55', 'points': 50, 'need': [{'pid': 12, 'amount': 10}, {'pid': 10, 'amount': 10}, {'pid': 352, 'amount': 30}], 'duration': 11520}, '2': {'pid': '481', 'amount': '51', 'points': 55, 'need': [{'pid': 355, 'amount': 12}, {'pid': 111, 'amount': 6}, {'pid': 359, 'amount': 25}], 'duration': 10800}, '3': {'pid': '482', 'amount': '55', 'points': 55, 'need': [{'pid': 356, 'amount': 20}, {'pid': 9, 'amount': 8}, {'pid': 1, 'amount': 200}], 'duration': 12240}, '4': {'pid': '483', 'amount': '36', 'points': 65, 'need': [{'pid': 10, 'amount': 10}, {'pid': 111, 'amount': 8}, {'pid': 127, 'amount': 15}], 'duration': 8640}}, '5': {'1': {'pid': '702', 'amount': '4438', 'points': 320, 'duration': 43200}, '2': {'pid': '703', 'amount': '8705', 'points': 2650, 'duration': 172800}, '3': {'pid': '704', 'amount': '4978', 'points': 210, 'duration': 28800}, '4': {'pid': '705', 'amount': '5227', 'points': 67, 'duration': 18000}, '5': {'pid': '706', 'amount': '4407', 'points': 540, 'duration': 36000}, '6': {'pid': '707', 'amount': '4381', 'points': 160, 'duration': 21600}, '7': {'pid': '708', 'amount': '4454', 'points': 215, 'duration': 14400}, '8': {'pid': '709', 'amount': '5277', 'points': 1300, 'duration': 86400}, '9': {'pid': '820', 'amount': '279', 'points': 8000, 'need': [{'pid': 12, 'amount': 4}, {'pid': 10, 'amount': 3}, {'pid': 20, 'amount': 60}], 'duration': 172800}, '10': {'pid': '821', 'amount': '390', 'points': 7300, 'need': [{'pid': 12, 'amount': 4}, {'pid': 10, 'amount': 8}, {'pid': 40, 'amount': 10}], 'duration': 259200}, '11': {'pid': '822', 'amount': '386', 'points': 6150, 'need': [{'pid': 12, 'amount': 3}, {'pid': 10, 'amount': 3}, {'pid': 33, 'amount': 20}], 'duration': 86400}, '12': {'pid': '823', 'amount': '226', 'points': 7100, 'need': [{'pid': 12, 'amount': 6}, {'pid': 352, 'amount': 15}, {'pid': 357, 'amount': 16}, {'pid': 355, 'amount': 6}], 'duration': 302400}, '13': {'pid': '824', 'amount': '125', 'points': 11900, 'need': [{'pid': 12, 'amount': 6}, {'pid': 360, 'amount': 12}, {'pid': 351, 'amount': 10}, {'pid': 359, 'amount': 15}], 'duration': 345600}, '14': {'pid': '825', 'amount': '547', 'points': 268, 'duration': 72000}, '15': {'pid': '826', 'amount': '68', 'points': 630, 'duration': 172800}, '16': {'pid': '827', 'amount': '500', 'points': 1590, 'duration': 432000}, '17': {'pid': '828', 'amount': '500', 'points': 258, 'duration': 68400}, '18': {'pid': '829', 'amount': '266', 'points': 3120, 'duration': 414000}, '19': {'pid': '830', 'amount': '34', 'points': 1205, 'duration': 306000}, '20': {'pid': '831', 'amount': '127', 'points': 635, 'duration': 172800}}, '6': {'1': {'pid': '832', 'amount': '210', 'points': 8420, 'duration': 468000}, '2': {'pid': '833', 'amount': '500', 'points': 580, 'duration': 154800}, '3': {'pid': '834', 'amount': '5', 'points': 1130, 'duration': 275400, 'oldtime': 306000}, '4': {'pid': '952', 'amount': '8112', 'points': 550, 'duration': 36000}, '5': {'pid': '953', 'amount': '8518', 'points': 1350, 'duration': 86400}, '6': {'pid': '957', 'amount': '8061', 'points': 650, 'duration': 81000}, '7': {'pid': '977', 'amount': '571', 'points': 1000, 'need': [{'pid': 957, 'amount': 14}, {'pid': 26, 'amount': 10}, {'pid': 34, 'amount': 11}], 'duration': 10800}, '8': {'pid': '978', 'amount': '794', 'points': 1000, 'need': [{'pid': 957, 'amount': 14}, {'pid': 17, 'amount': 11}, {'pid': 19, 'amount': 12}], 'duration': 10800}, '9': {'pid': '984', 'amount': '905', 'points': 1000, 'need': [{'pid': 950, 'amount': 8}, {'pid': 953, 'amount': 7}, {'pid': 12, 'amount': 5}], 'duration': 10800}, '10': {'pid': '1108', 'amount': '1206', 'points': 0, 'duration': 19800}, '11': {'pid': '1117', 'amount': '1262', 'points': 0, 'duration': 600}, '12': {'pid': '1118', 'amount': '4054', 'points': 0, 'duration': 720}, '13': {'pid': '1119', 'amount': '122', 'points': 0, 'duration': 720}, '14': {'pid': '1120', 'amount': '516', 'points': 0, 'duration': 120}, '15': {'pid': '1121', 'amount': '63', 'points': 0, 'duration': 240}, '16': {'pid': '1122', 'amount': '720', 'points': 0, 'duration': 300}, '17': {'pid': '1123', 'amount': '500', 'points': 0, 'duration': 360}, '18': {'pid': '1124', 'amount': '591', 'points': 0, 'duration': 420}, '19': {'pid': '1125', 'amount': '1084', 'points': 0, 'duration': 600}, '20': {'pid': '1200', 'amount': '2851', 'points': 225, 'duration': 43200}}, '7': {'1': {'pid': '1201', 'amount': '15', 'points': 3000, 'need': [{'pid': 1200, 'amount': 50}], 'duration': 86400}, '2': {'pid': '1202', 'amount': '2', 'points': 677, 'need': [{'pid': 1201, 'amount': 5}, {'pid': 20, 'amount': 20}], 'duration': 86400}, '3': {'pid': '1205', 'amount': '4', 'points': 888, 'need': [{'pid': 1201, 'amount': 5}, {'pid': 34, 'amount': 20}], 'duration': 97200}, '4': {'pid': '1206', 'amount': '3', 'points': 1209, 'need': [{'pid': 1201, 'amount': 5}, {'pid': 36, 'amount': 20}], 'duration': 100800}, '5': {'pid': '1208', 'amount': '6', 'points': 2052, 'need': [{'pid': 1201, 'amount': 5}, {'pid': 40, 'amount': 20}], 'duration': 111600}, '6': {'pid': '1210', 'amount': '1', 'points': 1274, 'need': [{'pid': 1201, 'amount': 5}, {'pid': 20, 'amount': 10}, {'pid': 360, 'amount': 10}], 'duration': 115200}, '7': {'pid': '1211', 'amount': '2', 'points': 1608, 'need': [{'pid': 1201, 'amount': 5}, {'pid': 351, 'amount': 20}], 'duration': 122400}, '8': {'pid': '1212', 'amount': '7', 'points': 5549, 'need': [{'pid': 1201, 'amount': 5}, {'pid': 356, 'amount': 20}], 'duration': 122400}, '9': {'pid': '1213', 'amount': '4', 'points': 1402, 'need': [{'pid': 1201, 'amount': 5}, {'pid': 361, 'amount': 10}, {'pid': 351, 'amount': 10}], 'duration': 115200}, '10': {'pid': '1214', 'amount': '4', 'points': 1757, 'need': [{'pid': 1201, 'amount': 5}, {'pid': 360, 'amount': 20}], 'duration': 129600}, '11': {'pid': '1217', 'amount': '1', 'points': 1632, 'need': [{'pid': 1201, 'amount': 5}, {'pid': 359, 'amount': 10}, {'pid': 355, 'amount': 10}], 'duration': 122400}, '12': {'pid': '1218', 'amount': '3', 'points': 2240, 'need': [{'pid': 1201, 'amount': 5}, {'pid': 354, 'amount': 10}, {'pid': 352, 'amount': 10}], 'duration': 144000}, '13': {'pid': '1219', 'amount': '4', 'points': 3990, 'need': [{'pid': 1201, 'amount': 5}, {'pid': 37, 'amount': 20}, {'pid': 1101, 'amount': 6}], 'duration': 151200}, '14': {'pid': '1220', 'amount': '1', 'points': 5412, 'need': [{'pid': 1201, 'amount': 5}, {'pid': 39, 'amount': 20}, {'pid': 1101, 'amount': 4}], 'duration': 158400}, '15': {'pid': '1222', 'amount': '1', 'points': 5082, 'need': [{'pid': 1201, 'amount': 5}, {'pid': 20, 'amount': 20}, {'pid': 1117, 'amount': 3}], 'duration': 151200}, '16': {'pid': '1223', 'amount': '7', 'points': 5580, 'need': [{'pid': 1201, 'amount': 5}, {'pid': 351, 'amount': 15}, {'pid': 353, 'amount': 10}, {'pid': 352, 'amount': 5}, {'pid': 1101, 'amount': 2}], 'duration': 165600}}}, '5': {'1': {'1': {'pid': '11', 'amount': '200', 'points': 1540, 'duration': 86400}, '2': {'pid': '28', 'amount': '4317', 'points': 5000, 'need': [{'pid': 11, 'amount': 8}], 'duration': 180000}, '3': {'pid': '151', 'amount': '8525', 'points': 5400, 'duration': 187200}, '4': {'pid': '152', 'amount': '6635', 'points': 7750, 'need': [{'pid': 151, 'amount': 5}], 'duration': 194400}, '5': {'pid': '153', 'amount': '720', 'points': 450, 'duration': 86400}, '6': {'pid': '155', 'amount': '60', 'points': 8250, 'need': [{'pid': 152, 'amount': 5}, {'pid': 28, 'amount': 6}], 'duration': 86400}, '7': {'pid': '156', 'amount': '54', 'points': 9725, 'need': [{'pid': 152, 'amount': 8}, {'pid': 28, 'amount': 5}], 'duration': 151200}, '8': {'pid': '157', 'amount': '63', 'points': 6250, 'need': [{'pid': 152, 'amount': 2}, {'pid': 28, 'amount': 9}], 'duration': 50400}, '9': {'pid': '351', 'amount': '180', 'points': 210, 'duration': 28800}, '10': {'pid': '352', 'amount': '240', 'points': 125, 'duration': 21600}, '11': {'pid': '353', 'amount': '107', 'points': 632, 'duration': 38880, 'oldtime': 43200}, '12': {'pid': '354', 'amount': '200', 'points': 634, 'duration': 43200}, '13': {'pid': '355', 'amount': '138', 'points': 265, 'duration': 79200}, '14': {'pid': '356', 'amount': '137', 'points': 2533, 'duration': 172800}, '15': {'pid': '357', 'amount': '360', 'points': 105, 'duration': 28800}, '16': {'pid': '358', 'amount': '90', 'points': 1270, 'duration': 86400}, '17': {'pid': '359', 'amount': '414', 'points': 425, 'duration': 28800}, '18': {'pid': '360', 'amount': '360', 'points': 635, 'duration': 86400}, '19': {'pid': '361', 'amount': '270', 'points': 162, 'duration': 10800}}}, '6': {'1': {'1': {'pid': '700', 'amount': '30', 'points': 635, 'duration': 43200}, '2': {'pid': '701', 'amount': '240', 'points': 80, 'duration': 21600}, '3': {'pid': '702', 'amount': '100', 'points': 320, 'duration': 43200}, '4': {'pid': '703', 'amount': '30', 'points': 2650, 'duration': 172800}, '5': {'pid': '704', 'amount': '60', 'points': 210, 'duration': 28800}, '6': {'pid': '705', 'amount': '120', 'points': 67, 'duration': 18000}, '7': {'pid': '706', 'amount': '300', 'points': 540, 'duration': 36000}, '8': {'pid': '707', 'amount': '180', 'points': 160, 'duration': 21600}, '9': {'pid': '708', 'amount': '20', 'points': 215, 'duration': 14400}}}, '7': [], '8': {'1': {'1': {'pid': '9', 'amount': '681', 'points': 750, 'duration': 14400}, '2': {'pid': '12', 'amount': '500', 'points': 2350, 'duration': 172800}, '3': {'pid': '17', 'amount': '500', 'points': 1, 'duration': 810, 'oldtime': 900}, '4': {'pid': '18', 'amount': '4050', 'points': 7, 'duration': 5400}, '5': {'pid': '19', 'amount': '500', 'points': 24, 'bonus': 600, 'duration': 13800}, '6': {'pid': '22', 'amount': '844', 'points': 52, 'bonus': 1200, 'duration': 28800}, '7': {'pid': '26', 'amount': '500', 'points': 108, 'bonus': 1800, 'duration': 45000}, '8': {'pid': '34', 'amount': '500', 'points': 157, 'bonus': 1800, 'duration': 46200}, '9': {'pid': '42', 'amount': '734', 'points': 6028, 'bonus': 3600, 'duration': 368400}, '10': {'pid': '112', 'amount': '663', 'points': 490, 'duration': 86400}, '11': {'pid': '115', 'amount': '820', 'points': 1174, 'duration': 306000}, '12': {'pid': '950', 'amount': '1765', 'points': 500, 'duration': 32400}, '13': {'pid': '951', 'amount': '5384', 'points': 1100, 'duration': 36000}, '14': {'pid': '952', 'amount': '818', 'points': 550, 'duration': 36000}, '15': {'pid': '953', 'amount': '332', 'points': 1350, 'duration': 86400}, '16': {'pid': '955', 'amount': '6132', 'points': 600, 'duration': 75600}, '17': {'pid': '957', 'amount': '866', 'points': 650, 'duration': 81000}, '18': {'pid': '973', 'amount': '73', 'points': 750, 'need': [{'pid': 950, 'amount': 7}, {'pid': 952, 'amount': 7}, {'pid': 9, 'amount': 7}], 'duration': 9000}, '19': {'pid': '974', 'amount': '73', 'points': 750, 'need': [{'pid': 952, 'amount': 14}, {'pid': 9, 'amount': 9}, {'pid': 115, 'amount': 11}], 'duration': 8100}, '20': {'pid': '977', 'amount': '8', 'points': 1000, 'need': [{'pid': 957, 'amount': 14}, {'pid': 26, 'amount': 10}, {'pid': 34, 'amount': 11}], 'duration': 10800}}, '2': {'1': {'pid': '978', 'amount': '751', 'points': 1000, 'need': [{'pid': 957, 'amount': 14}, {'pid': 17, 'amount': 11}, {'pid': 19, 'amount': 12}], 'duration': 10800}, '2': {'pid': '979', 'amount': '72', 'points': 750, 'need': [{'pid': 952, 'amount': 14}, {'pid': 22, 'amount': 9}, {'pid': 112, 'amount': 13}], 'duration': 8100}, '3': {'pid': '982', 'amount': '73', 'points': 750, 'need': [{'pid': 951, 'amount': 14}, {'pid': 12, 'amount': 9}, {'pid': 42, 'amount': 11}], 'duration': 8100}, '4': {'pid': '984', 'amount': '1695', 'points': 1000, 'need': [{'pid': 950, 'amount': 8}, {'pid': 953, 'amount': 7}, {'pid': 12, 'amount': 5}], 'duration': 10800}}}, '9': {'1': {'1': {'pid': '20', 'amount': '610', 'points': 42, 'bonus': 1200, 'duration': 27600}, '2': {'pid': '33', 'amount': '840', 'points': 229, 'bonus': 3600, 'duration': 68400}, '3': {'pid': '34', 'amount': '430', 'points': 157, 'bonus': 1800, 'duration': 46200}, '4': {'pid': '38', 'amount': '930', 'points': 211, 'bonus': 1800, 'duration': 55800}, '5': {'pid': '357', 'amount': '10', 'points': 105, 'duration': 28800}, '6': {'pid': '1200', 'amount': '1530', 'points': 225, 'duration': 43200}, '7': {'pid': '1201', 'amount': '40', 'points': 3000, 'need': [{'pid': 1200, 'amount': 50}], 'duration': 86400}, '8': {'pid': '1202', 'amount': '50', 'points': 677, 'need': [{'pid': 1201, 'amount': 5}, {'pid': 20, 'amount': 20}], 'duration': 86400}, '9': {'pid': '1204', 'amount': '60', 'points': 863, 'need': [{'pid': 1201, 'amount': 5}, {'pid': 33, 'amount': 20}], 'duration': 93600}, '10': {'pid': '1205', 'amount': '40', 'points': 888, 'need': [{'pid': 1201, 'amount': 5}, {'pid': 34, 'amount': 20}], 'duration': 97200}, '11': {'pid': '1207', 'amount': '50', 'points': 1059, 'need': [{'pid': 1201, 'amount': 5}, {'pid': 38, 'amount': 20}], 'duration': 108000}}}, '10': {'1': {'1': {'pid': '1101', 'amount': '500', 'points': 400, 'duration': 25200}, '2': {'pid': '1102', 'amount': '500', 'points': 605, 'duration': 36000}, '3': {'pid': '1103', 'amount': '500', 'points': 500, 'duration': 86400}, '4': {'pid': '1104', 'amount': '419', 'points': 800, 'duration': 100800}, '5': {'pid': '1105', 'amount': '500', 'points': 970, 'duration': 108000}, '6': {'pid': '1107', 'amount': '19', 'points': 0, 'duration': 19800}, '7': {'pid': '1108', 'amount': '29', 'points': 0, 'duration': 19800}, '8': {'pid': '1109', 'amount': '9', 'points': 0, 'duration': 19800}, '9': {'pid': '1111', 'amount': '56', 'points': 0, 'duration': 19800}, '10': {'pid': '1112', 'amount': '24', 'points': 0, 'duration': 19800}, '11': {'pid': '1113', 'amount': '32', 'points': 0, 'duration': 19800}, '12': {'pid': '1115', 'amount': '29', 'points': 0, 'duration': 19800}, '13': {'pid': '1117', 'amount': '110', 'points': 0, 'duration': 600}, '14': {'pid': '1118', 'amount': '640', 'points': 0, 'duration': 720}, '15': {'pid': '1119', 'amount': '60', 'points': 0, 'duration': 720}, '16': {'pid': '1120', 'amount': '5225', 'points': 0, 'duration': 120}, '17': {'pid': '1121', 'amount': '2792', 'points': 0, 'duration': 240}, '18': {'pid': '1122', 'amount': '3992', 'points': 0, 'duration': 300}, '19': {'pid': '1123', 'amount': '1150', 'points': 0, 'duration': 360}, '20': {'pid': '1124', 'amount': '576', 'points': 0, 'duration': 420}}, '2': {'1': {'pid': '1125', 'amount': '3133', 'points': 0, 'duration': 600}}}}, 'harvestbonus': {'32': {'pointbonus': 14}}, 'tempstock': {'11': {'5': '200'}, '28': {'5': '4317'}, '151': {'5': '8525'}, '152': {'5': '6635'}, '153': {'5': '720'}, '155': {'5': '60'}, '156': {'5': '54'}, '157': {'5': '63'}, '351': {'5': '180'}, '352': {'5': '240'}, '353': {'5': '107'}, '354': {'5': '200'}, '355': {'5': '138'}, '356': {'5': '137'}, '357': {'5': '360', '9': '10'}, '358': {'5': '90'}, '359': {'5': '414'}, '360': {'5': '360'}, '361': {'5': '270'}, '700': {'6': '30'}, '701': {'6': '240'}, '702': {'6': '100'}, '703': {'6': '30'}, '704': {'6': '60'}, '705': {'6': '120'}, '706': {'6': '300'}, '707': {'6': '180'}, '708': {'6': '20'}, '9': {'8': '681'}, '12': {'8': '500'}, '17': {'8': '500'}, '18': {'8': '4050'}, '19': {'8': '500'}, '22': {'8': '844'}, '26': {'8': '500'}, '34': {'8': '500', '9': '430'}, '42': {'8': '734'}, '112': {'8': '663'}, '115': {'8': '820'}, '950': {'8': '1765'}, '951': {'8': '5384'}, '952': {'8': '818'}, '953': {'8': '332'}, '955': {'8': '6132'}, '957': {'8': '866'}, '973': {'8': '73'}, '974': {'8': '73'}, '977': {'8': '8'}, '978': {'8': '751'}, '979': {'8': '72'}, '982': {'8': '73'}, '984': {'8': '1695'}, '20': {'9': '610'}, '33': {'9': '840'}, '38': {'9': '930'}, '1200': {'9': '1530'}, '1201': {'9': '40'}, '1202': {'9': '50'}, '1204': {'9': '60'}, '1205': {'9': '40'}, '1207': {'9': '50'}, '1101': {'10': '500'}, '1102': {'10': '500'}, '1103': {'10': '500'}, '1104': {'10': '419'}, '1105': {'10': '500'}, '1107': {'10': '19'}, '1108': {'10': '29'}, '1109': {'10': '9'}, '1111': {'10': '56'}, '1112': {'10': '24'}, '1113': {'10': '32'}, '1115': {'10': '29'}, '1117': {'10': '110'}, '1118': {'10': '640'}, '1119': {'10': '60'}, '1120': {'10': '5225'}, '1121': {'10': '2792'}, '1122': {'10': '3992'}, '1123': {'10': '1150'}, '1124': {'10': '576'}, '1125': {'10': '3133'}}, 'racklock': {'8': {'house': 0, 'text': 'Benötigt 100 von Rosis Einkäufen', 'premium': 1}, '9': {'house': 0, 'text': 'Benötigt Pflanzenregal 8', 'premium': 1}, '10': {'house': 0, 'text': 'Benötigt Pflanzenregal 9', 'premium': 1}, '11': {'house': 0, 'text': 'Benötigt Pflanzenregal 10', 'premium': 1}, '15': {'house': 1, 'cost': '48700', 'text': 'Regal für 48.700,00&nbsp;kT kaufen', 'premium': 1}}, 'rackfarm': 0}, 'menue': {'success': 1, 'bar': '4326603.58', 'points': '12296309935', 'coins': '461', 'level': 'Freilandfürst', 'levelnum': '61', 'premium': 1, 'premium_bis': '04.11.27, 14:26 Uhr', 'tutorial': 0, 'levelcurrentlimit': 11719330000, 'levellimit': 12719423000, 'mail': '0', 'contracts': '0', 'lng_con': 'Vertr&auml;ge', 'lng_mail': 'Nachrichten', 'time': 1794242983, 'guildid': 0, 'guildname': 0, 'guildlevel': 0, 'guildmemberstatus': 0, 'localtime': '2026, 11, 9, 17, 49, 43', 'servertime': 1794242983, 'reallocatebuilding': 1, 'reallocateanimals': 1, 'forestry_unlock': 1, 'farms': [], 'uname': 'Piginator', 'cropbonus': 0, 'donkey': 1, 'farmdog': 1, 'farmdog_harvest': 1, 'no_farmslide': 0, 'animalghosts': 0, 'main_flyer': 0, 'disable_autologout': 1, 'carpentry_bonus': 0, 'carpentrybonus_data': {'data': 0, 'config': {'level': 10, 'duration': 86400, 'needs': {'23': 26, '2': 91}, 'bonus': 1}, 'buildingid': 17}, 'paymentitem_remain': 0, 'specialoffer': {'active': 1, 'today': 0, 'remain': 281416}, 'specialbonus': 0, 'collectevent': 0, 'tinkergame': 0, 'giftevent': 0, 'teaser': {'farm10': 1}, 'loginevent': 0, 'loginbonus': {'data': {'count': 2, 'rewards': {'1': {'money': 1000, 'done': 1794092767}, '2': {'products': {'111': 2}, 'done': 1794179377}}, 'last': 1794179331, 'loginlog': [1791669859, 1791756385, 1791842915, 1791928802, 1792015773, 1792101649, 1792188167, 1792274923, 1792361243, 1792447288, 1792533911, 1792620264, 1792706413, 1792792957, 1792879673, 1792969566, 1793055790, 1793142456, 1793228616, 1793314900, 1793401704, 1793487931, 1793574139, 1793660817, 1793747382, 1793833654, 1793920210, 1794006370, 1794092719, 1794179331], 'bonus': {'pid': 21, 'start': 1791016198, 'remain': -2621985}}, 'firstdailyvisit': 0, 'products': [1, 2, 3, 4, 5, 6, 7, 8, 17, 18, 19, 20, 21, 22, 23, 24, 26, 29, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 108, 109, 112, 113, 114, 115, 126, 127, 128, 153, 154, 351, 352, 353, 354, 355, 356, 357, 358, 359, 360, 361, 700, 701, 702, 703, 704, 705, 706, 707, 708, 709, 825, 826, 827, 828, 829, 830, 831, 832, 833, 834, 950, 951, 952, 953, 954, 955, 957, 1200], 'config': {'rewards': {'1': {'type': 'money', 'amount': 100}, '2': {'type': 'products', 'amount': 100}, '3': {'type': 'money', 'amount': 100}, '4': {'type': 'products', 'amount': 100}, '5': {'type': 'money', 'amount': 100}, '6': {'type': 'products', 'amount': 100}, '7': {'type': 'plant', 'amount': 1}}, 'bonus': 0.2, 'bonus_duration': 604800}}, 'deliveryevent': 0, 'rainbowevent': 0, 'nutcracker': 0, 'coinsevent': 0, 'pentecostevent': 0, 'oktoberfest': 0, 'news': [], 'buildingOverviewToken': 1}, 'seasonpass': {'data': {'id': '4926', 'unr': '17626250', 'name': 'season10', 'points': '1600', 'buydate': '0', 'buypoints': '0', 'rewards': {'1': {'free': {'time': 1793752003}}, '4': {'free': {'time': 1794196124}}}, 'lasttasks': '1794179328', 'teaser': '1793487935', 'tutorial': '1793751995', 'createdate': '1793487929', 'remain': 7193416, 'tasks': [], 'tasksDone': [{'id': '661882', 'unr': '17626250', 'name': 'season10', 'type': 'forestryharvest', 'data': {'building': 1, 'pid': 7, 'count': 1, 'points': 100, 'done': 1}, 'createdate': '1794179328', 'finishdate': '1794193156'}, {'id': '661883', 'unr': '17626250', 'name': 'season10', 'type': 'foodworldharvestproduction', 'data': {'building': 4, 'pid': 451, 'count': 1, 'points': 100, 'done': 4}, 'createdate': '1794179328', 'finishdate': '1794182835'}, {'id': '661145', 'unr': '17626250', 'name': 'season10', 'type': 'foodworldharvestproduction', 'data': {'building': 2, 'pid': 136, 'count': 1, 'points': 100, 'done': 3}, 'createdate': '1794092716', 'finishdate': '1794098283'}, {'id': '661144', 'unr': '17626250', 'name': 'season10', 'type': 'startproduction', 'data': {'building': 2, 'pid': 9, 'count': 1, 'points': 100, 'done': 216}, 'createdate': '1794092716', 'finishdate': '1794095530'}, {'id': '660424', 'unr': '17626250', 'name': 'season10', 'type': 'water', 'data': {'building': 1, 'pid': 0, 'count': 5, 'points': 100, 'done': 120}, 'createdate': '1794006367', 'finishdate': '1794007083'}, {'id': '659687', 'unr': '17626250', 'name': 'season10', 'type': 'water', 'data': {'building': 1, 'pid': 0, 'count': 5, 'points': 100, 'done': 120}, 'createdate': '1793920207', 'finishdate': '1793920934'}, {'id': '659686', 'unr': '17626250', 'name': 'season10', 'type': 'farmi', 'data': {'count': 1, 'points': 100, 'done': 1}, 'createdate': '1793920207', 'finishdate': '1793920281'}, {'id': '658943', 'unr': '17626250', 'name': 'season10', 'type': 'startwindmillproduction', 'data': {'count': 1, 'points': 100, 'done': 1}, 'createdate': '1793833651', 'finishdate': '1793847229'}, {'id': '658942', 'unr': '17626250', 'name': 'season10', 'type': 'farmi', 'data': {'count': 1, 'points': 100, 'done': 1}, 'createdate': '1793833651', 'finishdate': '1793833736'}, {'id': '658213', 'unr': '17626250', 'name': 'season10', 'type': 'harvestproduction', 'data': {'building': 2, 'pid': 9, 'count': 1, 'points': 100, 'done': 22}, 'createdate': '1793747379', 'finishdate': '1793749480'}, {'id': '658212', 'unr': '17626250', 'name': 'season10', 'type': 'friendvisit', 'data': {'count': 1, 'points': 100, 'done': 1}, 'createdate': '1793747379', 'finishdate': '1793747455'}, {'id': '657435', 'unr': '17626250', 'name': 'season10', 'type': 'foodworldharvestproduction', 'data': {'building': 4, 'pid': 463, 'count': 1, 'points': 100, 'done': 5}, 'createdate': '1793660813', 'finishdate': '1793672327'}, {'id': '656665', 'unr': '17626250', 'name': 'season10', 'type': 'startwindmillproduction', 'data': {'count': 1, 'points': 100, 'done': 1}, 'createdate': '1793574136', 'finishdate': '1793638382'}, {'id': '656664', 'unr': '17626250', 'name': 'season10', 'type': 'harvest', 'data': {'building': 1, 'pid': 33, 'count': 24, 'points': 100, 'done': 480}, 'createdate': '1793574136', 'finishdate': '1793628024'}, {'id': '655885', 'unr': '17626250', 'name': 'season10', 'type': 'plant', 'data': {'building': 1, 'pid': 112, 'count': 24, 'points': 100, 'done': 120}, 'createdate': '1793487929', 'finishdate': '1793501570'}, {'id': '655884', 'unr': '17626250', 'name': 'season10', 'type': 'forestryplant', 'data': {'building': 1, 'pid': 7, 'count': 1, 'points': 100, 'done': 1}, 'createdate': '1793487929', 'finishdate': '1793488010'}]}, 'config': {'constants': {'cost': 100, 'minlevel': 10, 'step_width': 110, 'premium_points_bonus': 0.2, 'finish_coin_points': 200, 'finish_resttime': 3888000, 'daily_task_xp': 100, 'max_daily_tasks': 2, 'task_duration': 172800, 'tasks': ['plant', 'harvest', 'water', 'startproduction', 'harvestproduction', 'forestryplant', 'forestryharvest', 'forestrywater', 'foodworldstartproduction', 'foodworldharvestproduction', 'farmi', 'weather', 'startwindmillproduction', 'friendvisit']}, 'building_mapping': {'plant': [1], 'harvest': [1], 'water': [1], 'startproduction': [2, 3, 4, 5, 11, 12, 15], 'harvestproduction': [2, 3, 4, 5, 11, 12, 15]}, 'building_forestry_mapping': {'forestryplant': [1], 'forestrywater': [1], 'forestryharvest': [1]}, 'types': {'season1': {'start': 1722463200, 'end': 1730415599, 'theme': 'autumn'}, 'season2': {'start': 1730415600, 'end': 1738364399, 'theme': 'winter'}, 'season3': {'start': 1738364400, 'end': 1746050399, 'theme': 'spring'}, 'season4': {'start': 1746050400, 'end': 1753999199, 'theme': 'summer'}, 'season5': {'start': 1753999200, 'end': 1761951599, 'theme': 'autumn'}, 'season6': {'start': 1761951600, 'end': 1769900399, 'theme': 'winter'}, 'season7': {'start': 1769900400, 'end': 1777586399, 'theme': 'spring'}, 'season8': {'start': 1777586400, 'end': 1785535199, 'theme': 'summer'}, 'season9': {'start': 1785535200, 'end': 1793487599, 'theme': 'autumn'}, 'season10': {'start': 1793487600, 'end': 1801436399, 'theme': 'autumn'}}, 'steps': {'season1': {'1': {'points': 0, 'free': {'greenhouse': {'plant6': 1}}, 'premium': {'greenhouse': {'plant10': 1}}}, '2': {'points': 500, 'premium': {'friendpoints': 30}}, '3': {'points': 1000, 'premium': {'greenhouse': {'plant8': 1}}}, '4': {'points': 1500, 'free': {'farmdeco': {'pumpkins': 2}}, 'premium': {'package': {'3': 1}}}, '5': {'points': 2000, 'premium': {'greenhouse': {'plant10': 1}}}, '6': {'points': 2500, 'premium': {'paymentitemcollectionitem': 5}}, '7': {'points': 3000, 'free': {'greenhouse': {'plant2': 1}}, 'premium': {'greenhouse': {'plant8': 1}}}, '8': {'points': 3500, 'premium': {'farmdeco': {'statue': 1}}}, '9': {'points': 4000, 'premium': {'greenhouse': {'plant9': 1}}}, '10': {'points': 4500, 'free': {'greenhouse': {'plant6': 2}}, 'premium': {'greenhouse': {'pot7': 3}}}, '11': {'points': 5000, 'premium': {'greenhouse': {'plant8': 2}}}, '12': {'points': 5500, 'premium': {'paymentitemcollectionitem': 5}}, '13': {'points': 6000, 'free': {'package': {'3': 1}}, 'premium': {'greenhouse': {'plant9': 1}}}, '14': {'points': 6500, 'premium': {'package': {'4': 1}}}, '15': {'points': 7000, 'premium': {'gift': [1432]}}, '16': {'points': 7500, 'free': {'greenhouse': {'plant10': 1}}, 'premium': {'greenhouse': {'plant11': 1}}}, '17': {'points': 8000, 'premium': {'paymentitemcollectionitem': 5}}, '18': {'points': 8500, 'premium': {'greenhouse': {'plant9': 2}}}, '19': {'points': 9000, 'free': {'greenhouse': {'plant7': 1}}, 'premium': {'greenhouse': {'pot8': 3}}}, '20': {'points': 9500, 'premium': {'greenhouse': {'plant11': 3}}}, '21': {'points': 10000, 'premium': {'package': {'5': 1}}}, '22': {'points': 10500, 'free': {'greenhouse': {'plant3': 1}}, 'premium': {'greenhouse': {'plant12': 1}}}, '23': {'points': 11000, 'premium': {'paymentitemcollectionitem': 5}}, '24': {'points': 11500, 'premium': {'greenhouse': {'plant11': 1}}}, '25': {'points': 12000, 'free': {'package': {'4': 1}}, 'premium': {'powerup': {'36': 1}}}, '26': {'points': 12500, 'premium': {'greenhouse': {'plant12': 1}}}, '27': {'points': 13000, 'premium': {'paymentitemcollectionitem': 10}}, '28': {'points': 13500, 'free': {'greenhouse': {'plant7': 2}}, 'premium': {'greenhouse': {'plant12': 1}}}, '29': {'points': 14000, 'premium': {'farmdeco': {'sheepbench': 2}}}, '30': {'points': 14500, 'free': {'premium': 7}, 'premium': {'gift': [1433]}}}, 'season2': {'1': {'points': 0, 'free': {'greenhouse': {'plant13': 1}}, 'premium': {'greenhouse': {'plant17': 1}}}, '2': {'points': 500, 'premium': {'friendpoints': 30}}, '3': {'points': 1000, 'premium': {'greenhouse': {'plant14': 1}}}, '4': {'points': 1500, 'free': {'farmdeco': {'herbspiral': 2}}, 'premium': {'package': {'3': 1}}}, '5': {'points': 2000, 'premium': {'greenhouse': {'plant17': 1}}}, '6': {'points': 2500, 'premium': {'paymentitemcollectionitem': 5}}, '7': {'points': 3000, 'free': {'greenhouse': {'plant1': 1}}, 'premium': {'greenhouse': {'plant14': 1}}}, '8': {'points': 3500, 'premium': {'farmdeco': {'sled': 1}}}, '9': {'points': 4000, 'premium': {'greenhouse': {'plant16': 1}}}, '10': {'points': 4500, 'free': {'greenhouse': {'plant13': 2}}, 'premium': {'greenhouse': {'pot11': 3}}}, '11': {'points': 5000, 'premium': {'greenhouse': {'plant14': 2}}}, '12': {'points': 5500, 'premium': {'paymentitemcollectionitem': 5}}, '13': {'points': 6000, 'free': {'package': {'3': 1}}, 'premium': {'greenhouse': {'plant16': 1}}}, '14': {'points': 6500, 'premium': {'package': {'4': 1}}}, '15': {'points': 7000, 'premium': {'gift': [1476]}}, '16': {'points': 7500, 'free': {'greenhouse': {'plant17': 1}}, 'premium': {'greenhouse': {'pot13': 2}}}, '17': {'points': 8000, 'premium': {'paymentitemcollectionitem': 5}}, '18': {'points': 8500, 'premium': {'greenhouse': {'plant16': 2}}}, '19': {'points': 9000, 'free': {'greenhouse': {'plant15': 1}}, 'premium': {'greenhouse': {'pot12': 3}}}, '20': {'points': 9500, 'premium': {'greenhouse': {'plant18': 1}}}, '21': {'points': 10000, 'premium': {'package': {'5': 1}}}, '22': {'points': 10500, 'free': {'greenhouse': {'plant3': 1}}, 'premium': {'greenhouse': {'plant19': 3}}}, '23': {'points': 11000, 'premium': {'paymentitemcollectionitem': 5}}, '24': {'points': 11500, 'premium': {'greenhouse': {'plant18': 1}}}, '25': {'points': 12000, 'free': {'package': {'4': 1}}, 'premium': {'powerup': {'36': 1}}}, '26': {'points': 12500, 'premium': {'greenhouse': {'plant19': 2}}}, '27': {'points': 13000, 'premium': {'paymentitemcollectionitem': 10}}, '28': {'points': 13500, 'free': {'greenhouse': {'plant15': 2}}, 'premium': {'greenhouse': {'plant19': 1}}}, '29': {'points': 14000, 'premium': {'farmdeco': {'snowman': 2}}}, '30': {'points': 14500, 'free': {'premium': 7}, 'premium': {'gift': [1477]}}}, 'season3': {'1': {'points': 0, 'free': {'greenhouseroom': 4}, 'premium': {'greenhouse': {'plant24': 1}}}, '2': {'points': 500, 'premium': {'friendpoints': 30}}, '3': {'points': 1000, 'premium': {'greenhouse': {'plant21': 1}}}, '4': {'points': 1500, 'free': {'farmdeco': {'tulips': 2}}, 'premium': {'package': {'3': 1}}}, '5': {'points': 2000, 'premium': {'greenhouse': {'plant24': 1}}}, '6': {'points': 2500, 'premium': {'paymentitemcollectionitem': 5}}, '7': {'points': 3000, 'free': {'greenhouse': {'plant1': 1}}, 'premium': {'greenhouse': {'plant21': 1}}}, '8': {'points': 3500, 'premium': {'farmdeco': {'gardentable': 1}}}, '9': {'points': 4000, 'premium': {'greenhouse': {'plant23': 1}}}, '10': {'points': 4500, 'free': {'greenhouse': {'plant20': 2}}, 'premium': {'greenhouse': {'pot14': 3}}}, '11': {'points': 5000, 'premium': {'greenhouse': {'plant21': 2}}}, '12': {'points': 5500, 'premium': {'paymentitemcollectionitem': 5}}, '13': {'points': 6000, 'free': {'package': {'3': 1}}, 'premium': {'greenhouse': {'plant23': 1}}}, '14': {'points': 6500, 'premium': {'package': {'4': 1}}}, '15': {'points': 7000, 'premium': {'gift': [1520]}}, '16': {'points': 7500, 'free': {'greenhouse': {'plant24': 1}}, 'premium': {'greenhouse': {'pot16': 2}}}, '17': {'points': 8000, 'premium': {'paymentitemcollectionitem': 5}}, '18': {'points': 8500, 'premium': {'greenhouse': {'plant23': 2}}}, '19': {'points': 9000, 'free': {'greenhouse': {'plant22': 1}}, 'premium': {'greenhouse': {'pot15': 3}}}, '20': {'points': 9500, 'premium': {'greenhouse': {'plant26': 1}}}, '21': {'points': 10000, 'premium': {'package': {'5': 1}}}, '22': {'points': 10500, 'free': {'greenhouse': {'plant3': 1}}, 'premium': {'greenhouse': {'plant25': 3}}}, '23': {'points': 11000, 'premium': {'paymentitemcollectionitem': 5}}, '24': {'points': 11500, 'premium': {'greenhouse': {'plant26': 1}}}, '25': {'points': 12000, 'free': {'package': {'4': 1}}, 'premium': {'powerup': {'36': 1}}}, '26': {'points': 12500, 'premium': {'greenhouse': {'plant25': 2}}}, '27': {'points': 13000, 'premium': {'paymentitemcollectionitem': 10}}, '28': {'points': 13500, 'free': {'greenhouse': {'plant22': 2}}, 'premium': {'greenhouse': {'plant25': 1}}}, '29': {'points': 14000, 'premium': {'farmdeco': {'butterflies': 2}}}, '30': {'points': 14500, 'free': {'premium': 7}, 'premium': {'gift': [1521]}}}, 'season4': {'1': {'points': 0, 'free': {'greenhouse': {'pot20': 2}}, 'premium': {'greenhouse': {'plant31': 1}}}, '2': {'points': 500, 'premium': {'friendpoints': 30}}, '3': {'points': 1000, 'premium': {'greenhouse': {'plant28': 1}}}, '4': {'points': 1500, 'free': {'farmdeco': {'plantpots': 2}}, 'premium': {'package': {'3': 1}}}, '5': {'points': 2000, 'premium': {'greenhouse': {'plant31': 1}}}, '6': {'points': 2500, 'premium': {'paymentitemcollectionitem': 5}}, '7': {'points': 3000, 'free': {'greenhouse': {'plant1': 1}}, 'premium': {'greenhouse': {'plant28': 1}}}, '8': {'points': 3500, 'premium': {'farmdeco': {'loungechairs': 1}}}, '9': {'points': 4000, 'premium': {'greenhouse': {'plant30': 1}}}, '10': {'points': 4500, 'free': {'greenhouse': {'plant27': 2}}, 'premium': {'greenhouse': {'pot17': 2}}}, '11': {'points': 5000, 'premium': {'greenhouse': {'plant28': 2}}}, '12': {'points': 5500, 'premium': {'paymentitemcollectionitem': 5}}, '13': {'points': 6000, 'free': {'package': {'3': 1}}, 'premium': {'greenhouse': {'plant30': 1}}}, '14': {'points': 6500, 'premium': {'package': {'4': 1}}}, '15': {'points': 7000, 'premium': {'gift': [1541]}}, '16': {'points': 7500, 'free': {'greenhouse': {'plant31': 1}}, 'premium': {'greenhouse': {'pot20': 2}}}, '17': {'points': 8000, 'premium': {'paymentitemcollectionitem': 5}}, '18': {'points': 8500, 'premium': {'greenhouse': {'plant30': 2}}}, '19': {'points': 9000, 'free': {'greenhouse': {'plant29': 1}}, 'premium': {'greenhouse': {'pot18': 2}}}, '20': {'points': 9500, 'premium': {'greenhouse': {'plant33': 1}}}, '21': {'points': 10000, 'premium': {'package': {'5': 1}}}, '22': {'points': 10500, 'free': {'greenhouse': {'plant3': 1}}, 'premium': {'greenhouse': {'plant32': 3}}}, '23': {'points': 11000, 'premium': {'paymentitemcollectionitem': 5}}, '24': {'points': 11500, 'premium': {'greenhouse': {'plant33': 1}}}, '25': {'points': 12000, 'free': {'package': {'4': 1}}, 'premium': {'powerup': {'36': 1}}}, '26': {'points': 12500, 'premium': {'greenhouse': {'pot19': 3}}}, '27': {'points': 13000, 'premium': {'paymentitemcollectionitem': 10}}, '28': {'points': 13500, 'free': {'greenhouse': {'plant29': 2}}, 'premium': {'greenhouse': {'plant32': 1}}}, '29': {'points': 14000, 'premium': {'farmdeco': {'hammock': 1}}}, '30': {'points': 14500, 'free': {'premium': 7}, 'premium': {'gift': [1542]}}}, 'season5': {'1': {'points': 0, 'free': {'greenhouse': {'pot23': 2}}, 'premium': {'greenhouse': {'plant36': 2}}}, '2': {'points': 500, 'premium': {'friendpoints': 30}}, '3': {'points': 1000, 'premium': {'greenhouse': {'pot22': 2}}}, '4': {'points': 1500, 'free': {'farmdeco': {'thistle': 3}}, 'premium': {'package': {'3': 1}}}, '5': {'points': 2000, 'premium': {'greenhouse': {'plant39': 2}}}, '6': {'points': 2500, 'premium': {'paymentitemcollectionitem': 5}}, '7': {'points': 3000, 'free': {'greenhouse': {'plant10': 1}}, 'premium': {'greenhouse': {'plant35': 2}}}, '8': {'points': 3500, 'premium': {'farmdeco': {'woodchicken': 1}}}, '9': {'points': 4000, 'premium': {'greenhouse': {'pot21': 1}}}, '10': {'points': 4500, 'free': {'greenhouse': {'plant14': 1}}, 'premium': {'greenhouse': {'plant38': 1}}}, '11': {'points': 5000, 'premium': {'greenhouse': {'plant34': 2}}}, '12': {'points': 5500, 'premium': {'paymentitemcollectionitem': 5}}, '13': {'points': 6000, 'free': {'package': {'3': 1}}, 'premium': {'greenhouse': {'plant37': 2}}}, '14': {'points': 6500, 'premium': {'package': {'4': 1}}}, '15': {'points': 7000, 'premium': {'gift': [1572]}}, '16': {'points': 7500, 'free': {'greenhouse': {'pot23': 2}}, 'premium': {'greenhouse': {'pot22': 2}}}, '17': {'points': 8000, 'premium': {'paymentitemcollectionitem': 5}}, '18': {'points': 8500, 'premium': {'greenhouse': {'plant39': 2}}}, '19': {'points': 9000, 'free': {'greenhouse': {'plant36': 2}}, 'premium': {'greenhouse': {'pot21': 1}}}, '20': {'points': 9500, 'premium': {'greenhouse': {'plant38': 1}}}, '21': {'points': 10000, 'premium': {'package': {'5': 1}}}, '22': {'points': 10500, 'free': {'greenhouse': {'plant9': 1}}, 'premium': {'greenhouse': {'plant37': 2}}}, '23': {'points': 11000, 'premium': {'paymentitemcollectionitem': 5}}, '24': {'points': 11500, 'premium': {'greenhouse': {'plant35': 2}}}, '25': {'points': 12000, 'free': {'package': {'4': 1}}, 'premium': {'powerup': {'36': 1}}}, '26': {'points': 12500, 'premium': {'greenhouse': {'plant34': 2}}}, '27': {'points': 13000, 'premium': {'paymentitemcollectionitem': 10}}, '28': {'points': 13500, 'free': {'greenhouse': {'pot23': 2}}, 'premium': {'greenhouse': {'plant40': 2}}}, '29': {'points': 14000, 'premium': {'farmdeco': {'gramophone': 1}}}, '30': {'points': 14500, 'free': {'premium': 7}, 'premium': {'gift': [1573]}}}, 'season6': {'1': {'points': 0, 'free': {'greenhouse': {'pot26': 2}}, 'premium': {'greenhouse': {'plant41': 2}}}, '2': {'points': 500, 'premium': {'friendpoints': 30}}, '3': {'points': 1000, 'premium': {'greenhouse': {'pot25': 2}}}, '4': {'points': 1500, 'free': {'farmdeco': {'poinsettia': 2}}, 'premium': {'package': {'3': 1}}}, '5': {'points': 2000, 'premium': {'greenhouse': {'plant42': 2}}}, '6': {'points': 2500, 'premium': {'paymentitemcollectionitem': 5}}, '7': {'points': 3000, 'free': {'greenhouse': {'plant13': 2}}, 'premium': {'greenhouse': {'plant43': 2}}}, '8': {'points': 3500, 'premium': {'farmdeco': {'snowangel': 1}}}, '9': {'points': 4000, 'premium': {'greenhouse': {'pot24': 1}}}, '10': {'points': 4500, 'free': {'greenhouse': {'plant16': 2}}, 'premium': {'greenhouse': {'plant44': 1}}}, '11': {'points': 5000, 'premium': {'greenhouse': {'plant45': 2}}}, '12': {'points': 5500, 'premium': {'paymentitemcollectionitem': 5}}, '13': {'points': 6000, 'free': {'package': {'3': 1}}, 'premium': {'greenhouse': {'plant46': 2}}}, '14': {'points': 6500, 'premium': {'package': {'4': 1}}}, '15': {'points': 7000, 'premium': {'gift': [1616]}}, '16': {'points': 7500, 'free': {'greenhouse': {'pot26': 2}}, 'premium': {'greenhouse': {'pot25': 2}}}, '17': {'points': 8000, 'premium': {'paymentitemcollectionitem': 5}}, '18': {'points': 8500, 'premium': {'greenhouse': {'plant42': 2}}}, '19': {'points': 9000, 'free': {'greenhouse': {'plant41': 2}}, 'premium': {'greenhouse': {'pot24': 1}}}, '20': {'points': 9500, 'premium': {'greenhouse': {'plant44': 1}}}, '21': {'points': 10000, 'premium': {'package': {'5': 1}}}, '22': {'points': 10500, 'free': {'greenhouse': {'plant17': 2}}, 'premium': {'greenhouse': {'plant46': 2}}}, '23': {'points': 11000, 'premium': {'paymentitemcollectionitem': 5}}, '24': {'points': 11500, 'premium': {'greenhouse': {'plant43': 2}}}, '25': {'points': 12000, 'free': {'package': {'4': 1}}, 'premium': {'powerup': {'36': 1}}}, '26': {'points': 12500, 'premium': {'greenhouse': {'plant45': 2}}}, '27': {'points': 13000, 'premium': {'paymentitemcollectionitem': 10}}, '28': {'points': 13500, 'free': {'greenhouseroom': 5}, 'premium': {'greenhouse': {'plant47': 2}}}, '29': {'points': 14000, 'premium': {'farmdeco': {'trudistatue': 1}}}, '30': {'points': 14500, 'free': {'premium': 7}, 'premium': {'gift': [1617]}}}, 'season7': {'1': {'points': 0, 'free': {'greenhouse': {'pot29': 2}}, 'premium': {'greenhouse': {'plant48': 2}}}, '2': {'points': 500, 'premium': {'friendpoints': 30}}, '3': {'points': 1000, 'premium': {'greenhouse': {'pot28': 2}}}, '4': {'points': 1500, 'free': {'farmdeco': {'birdbath': 2}}, 'premium': {'package': {'3': 1}}}, '5': {'points': 2000, 'premium': {'greenhouse': {'plant49': 2}}}, '6': {'points': 2500, 'premium': {'paymentitemcollectionitem': 5}}, '7': {'points': 3000, 'free': {'greenhouse': {'plant20': 2}}, 'premium': {'greenhouse': {'plant50': 2}}}, '8': {'points': 3500, 'premium': {'farmdeco': {'hyacinth': 1}}}, '9': {'points': 4000, 'premium': {'greenhouse': {'pot29': 1}}}, '10': {'points': 4500, 'free': {'greenhouse': {'plant16': 2}}, 'premium': {'greenhouse': {'plant51': 1}}}, '11': {'points': 5000, 'premium': {'greenhouse': {'plant52': 2}}}, '12': {'points': 5500, 'premium': {'paymentitemcollectionitem': 5}}, '13': {'points': 6000, 'free': {'package': {'3': 1}}, 'premium': {'greenhouse': {'plant53': 2}}}, '14': {'points': 6500, 'premium': {'package': {'4': 1}}}, '15': {'points': 7000, 'premium': {'gift': [1658]}}, '16': {'points': 7500, 'free': {'greenhouse': {'pot27': 2}}, 'premium': {'greenhouse': {'pot27': 2}}}, '17': {'points': 8000, 'premium': {'paymentitemcollectionitem': 5}}, '18': {'points': 8500, 'premium': {'greenhouse': {'plant49': 2}}}, '19': {'points': 9000, 'free': {'greenhouse': {'plant48': 2}}, 'premium': {'greenhouse': {'pot29': 1}}}, '20': {'points': 9500, 'premium': {'greenhouse': {'plant51': 1}}}, '21': {'points': 10000, 'premium': {'package': {'5': 1}}}, '22': {'points': 10500, 'free': {'greenhouse': {'plant18': 2}}, 'premium': {'greenhouse': {'plant53': 2}}}, '23': {'points': 11000, 'premium': {'paymentitemcollectionitem': 5}}, '24': {'points': 11500, 'premium': {'greenhouse': {'plant50': 2}}}, '25': {'points': 12000, 'free': {'package': {'4': 1}}, 'premium': {'powerup': {'36': 1}}}, '26': {'points': 12500, 'premium': {'greenhouse': {'plant52': 2}}}, '27': {'points': 13000, 'premium': {'paymentitemcollectionitem': 10}}, '28': {'points': 13500, 'free': {'greenhouse': {'pot28': 2}}, 'premium': {'greenhouse': {'plant54': 2}}}, '29': {'points': 14000, 'premium': {'farmdeco': {'swing': 1}}}, '30': {'points': 14500, 'free': {'premium': 7}, 'premium': {'gift': [1659]}}}, 'season8': {'1': {'points': 0, 'free': {'greenhouse': {'pot32': 2}}, 'premium': {'greenhouse': {'plant55': 2}}}, '2': {'points': 500, 'premium': {'friendpoints': 30}}, '3': {'points': 1000, 'premium': {'greenhouse': {'pot31': 2}}}, '4': {'points': 1500, 'free': {'farmdeco': {'picknick': 2}}, 'premium': {'package': {'3': 1}}}, '5': {'points': 2000, 'premium': {'greenhouse': {'plant56': 2}}}, '6': {'points': 2500, 'premium': {'paymentitemcollectionitem': 5}}, '7': {'points': 3000, 'free': {'greenhouse': {'plant13': 2}}, 'premium': {'greenhouse': {'plant57': 2}}}, '8': {'points': 3500, 'premium': {'farmdeco': {'watermelon': 1}}}, '9': {'points': 4000, 'premium': {'greenhouse': {'pot32': 1}}}, '10': {'points': 4500, 'free': {'greenhouse': {'plant3': 2}}, 'premium': {'greenhouse': {'plant58': 1}}}, '11': {'points': 5000, 'premium': {'greenhouse': {'plant59': 2}}}, '12': {'points': 5500, 'premium': {'paymentitemcollectionitem': 5}}, '13': {'points': 6000, 'free': {'package': {'3': 1}}, 'premium': {'greenhouse': {'plant60': 2}}}, '14': {'points': 6500, 'premium': {'package': {'4': 1}}}, '15': {'points': 7000, 'premium': {'gift': [1695]}}, '16': {'points': 7500, 'free': {'greenhouse': {'pot30': 2}}, 'premium': {'greenhouse': {'pot30': 2}}}, '17': {'points': 8000, 'premium': {'paymentitemcollectionitem': 5}}, '18': {'points': 8500, 'premium': {'greenhouse': {'plant56': 2}}}, '19': {'points': 9000, 'free': {'greenhouse': {'plant55': 2}}, 'premium': {'greenhouse': {'pot32': 1}}}, '20': {'points': 9500, 'premium': {'greenhouse': {'plant58': 1}}}, '21': {'points': 10000, 'premium': {'package': {'5': 1}}}, '22': {'points': 10500, 'free': {'greenhouse': {'plant24': 2}}, 'premium': {'greenhouse': {'plant60': 2}}}, '23': {'points': 11000, 'premium': {'paymentitemcollectionitem': 5}}, '24': {'points': 11500, 'premium': {'greenhouse': {'plant57': 2}}}, '25': {'points': 12000, 'free': {'package': {'4': 1}}, 'premium': {'powerup': {'36': 1}}}, '26': {'points': 12500, 'premium': {'greenhouse': {'plant59': 2}}}, '27': {'points': 13000, 'premium': {'paymentitemcollectionitem': 10}}, '28': {'points': 13500, 'free': {'greenhouse': {'pot31': 2}}, 'premium': {'greenhouse': {'plant61': 2}}}, '29': {'points': 14000, 'premium': {'farmdeco': {'pool': 1}}}, '30': {'points': 14500, 'free': {'premium': 7}, 'premium': {'gift': [1696]}}}, 'season9': {'1': {'points': 0, 'free': {'greenhouse': {'pot35': 2}}, 'premium': {'greenhouse': {'plant62': 2}}}, '2': {'points': 500, 'premium': {'friendpoints': 30}}, '3': {'points': 1000, 'premium': {'greenhouse': {'pot34': 2}}}, '4': {'points': 1500, 'free': {'farmdeco': {'salesstand': 2}}, 'premium': {'package': {'3': 1}}}, '5': {'points': 2000, 'premium': {'greenhouse': {'plant63': 2}}}, '6': {'points': 2500, 'premium': {'paymentitemcollectionitem': 5}}, '7': {'points': 3000, 'free': {'greenhouse': {'plant20': 2}}, 'premium': {'greenhouse': {'plant64': 2}}}, '8': {'points': 3500, 'premium': {'farmdeco': {'heather': 1}}}, '9': {'points': 4000, 'premium': {'greenhouse': {'pot35': 1}}}, '10': {'points': 4500, 'free': {'greenhouse': {'plant3': 2}}, 'premium': {'greenhouse': {'plant65': 1}}}, '11': {'points': 5000, 'premium': {'greenhouse': {'plant66': 2}}}, '12': {'points': 5500, 'premium': {'paymentitemcollectionitem': 5}}, '13': {'points': 6000, 'free': {'package': {'3': 1}}, 'premium': {'greenhouse': {'plant67': 2}}}, '14': {'points': 6500, 'premium': {'package': {'4': 1}}}, '15': {'points': 7000, 'premium': {'gift': [1718]}}, '16': {'points': 7500, 'free': {'greenhouse': {'pot33': 2}}, 'premium': {'greenhouse': {'pot33': 2}}}, '17': {'points': 8000, 'premium': {'paymentitemcollectionitem': 5}}, '18': {'points': 8500, 'premium': {'greenhouse': {'plant63': 2}}}, '19': {'points': 9000, 'free': {'greenhouse': {'plant62': 2}}, 'premium': {'greenhouse': {'pot35': 1}}}, '20': {'points': 9500, 'premium': {'greenhouse': {'plant65': 1}}}, '21': {'points': 10000, 'premium': {'package': {'5': 1}}}, '22': {'points': 10500, 'free': {'greenhouse': {'plant12': 2}}, 'premium': {'greenhouse': {'plant67': 2}}}, '23': {'points': 11000, 'premium': {'paymentitemcollectionitem': 5}}, '24': {'points': 11500, 'premium': {'greenhouse': {'plant64': 2}}}, '25': {'points': 12000, 'free': {'package': {'4': 1}}, 'premium': {'powerup': {'36': 1}}}, '26': {'points': 12500, 'premium': {'greenhouse': {'plant66': 2}}}, '27': {'points': 13000, 'premium': {'paymentitemcollectionitem': 10}}, '28': {'points': 13500, 'free': {'greenhouse': {'pot34': 2}}, 'premium': {'greenhouse': {'plant68': 2}}}, '29': {'points': 14000, 'premium': {'farmdeco': {'pinwheels': 1}}}, '30': {'points': 14500, 'free': {'premium': 7}, 'premium': {'gift': [1719]}}}, 'season10': {'1': {'points': 0, 'free': {'greenhouse': {'pot39': 2}}, 'premium': {'greenhouse': {'plant69': 2}}}, '2': {'points': 500, 'premium': {'friendpoints': 30}}, '3': {'points': 1000, 'premium': {'greenhouse': {'pot38': 2}}}, '4': {'points': 1500, 'free': {'farmdeco': {'xmasgifts': 2}}, 'premium': {'package': {'3': 1}}}, '5': {'points': 2000, 'premium': {'greenhouse': {'plant70': 2}}}, '6': {'points': 2500, 'premium': {'paymentitemcollectionitem': 5}}, '7': {'points': 3000, 'free': {'greenhouse': {'plant27': 2}}, 'premium': {'greenhouse': {'plant71': 2}}}, '8': {'points': 3500, 'premium': {'farmdeco': {'igloo': 1}}}, '9': {'points': 4000, 'premium': {'greenhouse': {'pot39': 1}}}, '10': {'points': 4500, 'free': {'greenhouse': {'plant6': 2}}, 'premium': {'greenhouse': {'plant72': 1}}}, '11': {'points': 5000, 'premium': {'greenhouse': {'plant73': 2}}}, '12': {'points': 5500, 'premium': {'paymentitemcollectionitem': 5}}, '13': {'points': 6000, 'free': {'package': {'3': 1}}, 'premium': {'greenhouse': {'plant74': 2}}}, '14': {'points': 6500, 'premium': {'package': {'4': 1}}}, '15': {'points': 7000, 'premium': {'gift': [1759]}}, '16': {'points': 7500, 'free': {'greenhouse': {'pot37': 2}}, 'premium': {'greenhouse': {'pot36': 2}}}, '17': {'points': 8000, 'premium': {'paymentitemcollectionitem': 5}}, '18': {'points': 8500, 'premium': {'greenhouse': {'plant70': 2}}}, '19': {'points': 9000, 'free': {'greenhouse': {'plant69': 2}}, 'premium': {'greenhouse': {'pot39': 1}}}, '20': {'points': 9500, 'premium': {'greenhouse': {'plant72': 1}}}, '21': {'points': 10000, 'premium': {'package': {'5': 1}}}, '22': {'points': 10500, 'free': {'greenhouse': {'plant19': 2}}, 'premium': {'greenhouse': {'plant74': 2}}}, '23': {'points': 11000, 'premium': {'paymentitemcollectionitem': 5}}, '24': {'points': 11500, 'premium': {'greenhouse': {'plant71': 2}}}, '25': {'points': 12000, 'free': {'package': {'4': 1}}, 'premium': {'powerup': {'36': 1}}}, '26': {'points': 12500, 'premium': {'greenhouse': {'plant73': 2}}}, '27': {'points': 13000, 'premium': {'paymentitemcollectionitem': 10}}, '28': {'points': 13500, 'free': {'greenhouse': {'pot38': 2}}, 'premium': {'greenhouse': {'plant75': 2}}}, '29': {'points': 14000, 'premium': {'farmdeco': {'xmastree': 1}}}, '30': {'points': 14500, 'free': {'premium': 7}, 'premium': {'gift': [1760]}}}}}}, 'settlers': {'data': {'tutorial': {'start': 1792696031}}, 'entries': {'stonemason': 1}, 'quest': {'products': {'5': 70322, '2': 188200}, 'materials': {'sandstone': 10}, 'reward': {'points': 3850000}, 'delay': 86400, 'questid': 5, 'campaign': '1', 'remain': 40006, 'block': 1}, 'highestquest': '4', 'config': {'constants': {'minlevel': 53}, 'position': {'1': {'name': 'Steinmetz', 'money': 100000, 'entry': 'stonemason', 'quest': 4}, '2': {'name': 'Glasbläserei', 'entry': 'glassblowing', 'blocked': 1}, '3': {'blocked': 1}, '4': {'blocked': 1}, '5': {'blocked': 1}, '6': {'blocked': 1}}}}, 'mine': {'data': {'id': '16', 'unr': '17626250', 'workers_slots': {'2': 1793036440}, 'slots_slots': None, 'stock': {'sandstone': 1281, 'granite': 1159}, 'tutorial': {'start': 1792696077}, 'createdate': '1792484170', 'level': 1, 'workers': {'16': {'id': '16', 'unr': '17626250', 'position': '1', 'name': 'Skyler', 'energy': '100', 'data': {'sandstone': {'points': 113, 'level': 6, 'percent': 5}, 'granite': {'points': 1105, 'level': 14, 'percent': 52.77777777777778}}, 'img': {'back': 1, 'color': 3, 'body': 4, 'face': 4, 'eyes': 4, 'mouth': 9, 'nose': 6, 'hair': 1}, 'productionid': '16178', 'createdate': '1792484170'}, '402': {'id': '402', 'unr': '17626250', 'position': '2', 'name': 'Charlie', 'energy': '100', 'data': {'sandstone': {'points': 1144, 'level': 14, 'percent': 74.44444444444444}}, 'img': {'back': 1, 'color': 1, 'body': 6, 'face': 2, 'eyes': 7, 'mouth': 4, 'nose': 4, 'hair': 1}, 'productionid': '16114', 'createdate': '1793036440'}}, 'items': {'1304': {'id': '1304', 'unr': '17626250', 'name': 'pickaxe', 'durability': '19', 'stock': '1'}, '1305': {'id': '1305', 'unr': '17626250', 'name': 'pickaxe', 'durability': '19', 'stock': '1'}, '3163': {'id': '3163', 'unr': '17626250', 'name': 'shoes', 'durability': '19', 'stock': '1'}, '3164': {'id': '3164', 'unr': '17626250', 'name': 'shoes', 'durability': '19', 'stock': '1'}, '3165': {'id': '3165', 'unr': '17626250', 'name': 'shoes', 'durability': '20', 'stock': '0'}, '3166': {'id': '3166', 'unr': '17626250', 'name': 'shoes', 'durability': '20', 'stock': '0'}, '3172': {'id': '3172', 'unr': '17626250', 'name': 'shoes', 'durability': '20', 'stock': '0'}, '3173': {'id': '3173', 'unr': '17626250', 'name': 'shoes', 'durability': '20', 'stock': '0'}}, 'productions': {'1': {'1': {'id': '16114', 'unr': '17626250', 'slot': '1', 'position': '1', 'name': 'sandstone', 'amount': '18', 'workers': {'1': 402}, 'items': {'1': 3165, '2': 3166}, 'duration': '11880', 'reward': '', 'createdate': '1794236218', 'finishdate': '0', 'remain': 5115}}, '2': {'1': {'id': '16178', 'unr': '17626250', 'slot': '2', 'position': '1', 'name': 'granite', 'amount': '18', 'workers': {'1': 16}, 'items': {'1': 3172, '2': 3173}, 'duration': '14256', 'reward': '', 'createdate': '1794241574', 'finishdate': '0', 'remain': 12847}}}, 'validenergyproducts': {'1': 1, '2': 2, '3': 1, '4': 5, '5': 7, '6': 13, '7': 30, '8': 60, '9': 75, '10': 95, '11': 154, '12': 235, '17': 1, '18': 1, '19': 3, '20': 5, '21': 7, '22': 6, '23': 9, '24': 10, '25': 310, '26': 11, '27': 450, '28': 500, '29': 32, '30': 540, '31': 18, '32': 14, '33': 23, '34': 16, '35': 41, '36': 74, '37': 257, '38': 22, '39': 362, '40': 445, '41': 522, '42': 603, '43': 677, '44': 184, '91': 250, '92': 300, '93': 300, '108': 18, '109': 4, '110': 230, '111': 490, '112': 49, '113': 34, '114': 24, '115': 118, '126': 148, '127': 654, '128': 796, '144': 360, '151': 540, '152': 775, '153': 45, '154': 260, '155': 825, '156': 973, '157': 625, '159': 490, '160': 520, '350': 250, '820': 800, '821': 730, '822': 615, '823': 710, '824': 1190, '825': 27, '826': 63, '827': 159, '828': 26, '829': 312, '830': 121, '831': 64, '832': 842, '833': 58, '834': 113, '1200': 23, '1201': 300, '1202': 68, '1203': 79, '1204': 87, '1205': 89, '1206': 121, '1207': 106, '1208': 206, '1209': 177, '1210': 128, '1211': 161, '1212': 555, '1213': 141, '1214': 176, '1215': 181, '1216': 131, '1217': 164, '1218': 224, '1219': 399, '1220': 542, '1221': 537, '1222': 509, '1223': 558}, 'stats': {'harvest_all': '2474', 'harvest_granite': '1176', 'harvest_sandstone': '1298', 'mining_started': '184', 'shop_buy_item': '16'}, 'achievements': [1741, 1742]}, 'config': {'constants': {'workerimg': {'back': 1, 'color': 4, 'body': 6, 'face': 4, 'eyes': 10, 'mouth': 10, 'nose': 6, 'hair': 3}, 'worker_energy': 100, 'refill_products_points_energy': 10, 'speedup_mining': 7500}, 'slots': {'1': {'material': ['sandstone']}, '2': {'material': ['granite']}, '3': {'blocked': 1}, '4': {'blocked': 1}, '5': {'blocked': 1}, '6': {'blocked': 1}, '7': {'blocked': 1}, '8': {'blocked': 1}}, 'slots_slot': {'1': [], '2': {'money': 100000}, '3': {'coins': 50}, '4': {'rent': {'coins': 5, 'duration': 172800}}}, 'workers': {'1': [], '2': {'money': 1000, 'quest': 2}, '3': {'money': 10000, 'quest': 6}, '4': {'money': 100000, 'quest': 10}, '5': {'money': 1000000, 'quest': 15}, '6': {'coins': 10}, '7': {'coins': 25}, '8': {'coins': 50}}, 'items': {'gasdetector': {'name': 'Gasmessgerät', 'money': 2000, 'durability': 10, 'category': '', 'bonus': {'xp': 0.2, 'money': 0.02, 'points': 0.02}}, 'mask': {'name': 'Staubmaske', 'coins': 5, 'durability': 20, 'category': '', 'bonus': {'xp': 0.8, 'money': 0.1, 'points': 0.1}}, 'headlamp': {'name': 'Stirnlampe', 'money': 2500, 'durability': 20, 'category': '', 'bonus': {'xp': 0.2, 'money': 0.05, 'points': 0.05}}, 'hearingprotection': {'name': 'Gehörschutz', 'coins': 10, 'durability': 30, 'category': '', 'bonus': {'xp': 1, 'money': 0.2, 'points': 0.2}}, 'helmet': {'name': 'Sicherheitshelm', 'money': 3000, 'durability': 20, 'category': '', 'bonus': {'xp': 0.4, 'money': 0.08, 'points': 0.05}}, 'jacket': {'name': 'Warnjacke', 'coins': 15, 'durability': 20, 'category': '', 'bonus': {'xp': 1.2, 'money': 0.3, 'points': 0.3}}, 'pickaxe': {'name': 'Goldene Spitzhacke', 'money': 4000, 'durability': 20, 'category': '', 'bonus': {'xp': 0.4, 'money': 0.1, 'points': 0.05}}, 'robe': {'name': 'Sicherungsseil', 'coins': 20, 'durability': 20, 'category': '', 'bonus': {'xp': 1.4, 'money': 0.4, 'points': 0.4}}, 'shoes': {'name': 'Stahlkappenschuhe', 'money': 5000, 'durability': 20, 'category': '', 'bonus': {'xp': 0.8, 'money': 0.1, 'points': 0.05}}, 'walkietalkie': {'name': 'Funkgerät', 'coins': 25, 'durability': 20, 'category': '', 'bonus': {'xp': 1.6, 'money': 0.5, 'points': 0.5}}}, 'material': {'sandstone': {'name': 'Sandstein', 'duration': 18000, 'harvest': 5, 'energy': 10, 'worker': 1, 'items': 2, 'reward': {'points': 200, 'money': 150, 'xp': 5}, 'buy': {'1': 1, '4': 5, '8': 10}}, 'granite': {'name': 'Granit', 'duration': 21600, 'harvest': 5, 'energy': 15, 'worker': 1, 'items': 2, 'reward': {'points': 100, 'money': 300, 'xp': 5}, 'buy': {'1': 1, '4': 5, '8': 10}}}, 'workers_levels': {'1': {'points': 0}, '2': {'points': 10, 'bonus': {'duration': 0, 'harvest': 0.2}}, '3': {'points': 20, 'bonus': {'duration': 0, 'harvest': 0.4}}, '4': {'points': 35, 'bonus': {'duration': 0, 'harvest': 0.6}}, '5': {'points': 65, 'bonus': {'duration': 0.17, 'harvest': 0.8}}, '6': {'points': 110, 'bonus': {'duration': 0.17, 'harvest': 1}}, '7': {'points': 170, 'bonus': {'duration': 0.17, 'harvest': 1.2}}, '8': {'points': 245, 'bonus': {'duration': 0.17, 'harvest': 1.4}}, '9': {'points': 335, 'bonus': {'duration': 0.17, 'harvest': 1.6}}, '10': {'points': 440, 'bonus': {'duration': 0.34, 'harvest': 1.8}}, '11': {'points': 560, 'bonus': {'duration': 0.34, 'harvest': 2}}, '12': {'points': 695, 'bonus': {'duration': 0.34, 'harvest': 2.2}}, '13': {'points': 845, 'bonus': {'duration': 0.34, 'harvest': 2.4}}, '14': {'points': 1010, 'bonus': {'duration': 0.34, 'harvest': 2.6}}, '15': {'points': 1190, 'bonus': {'duration': 0.5, 'harvest': 2.8}}, '16': {'points': 1385, 'bonus': {'duration': 0.5, 'harvest': 3}}, '17': {'points': 1805, 'bonus': {'duration': 0.5, 'harvest': 3.2}}, '18': {'points': 2255, 'bonus': {'duration': 0.5, 'harvest': 3.4}}, '19': {'points': 2735, 'bonus': {'duration': 0.5, 'harvest': 3.6}}, '20': {'points': 3245, 'bonus': {'duration': 0.67, 'harvest': 4}}}, 'achievements': {'harvest_all': {'10': 1741, '500': 1742, '10000': 1743}}}}, 'stonemason': {'data': {'id': '104', 'unr': '17626250', 'points': '0', 'count': '22', 'count_alltime': '22', 'count_alltime_lastchange': '1794242736', 'count_buy': None, 'tools': {'1': 11}, 'tutorial': {'start': 1794196626}, 'createdate': '1794196605', 'level': 1, 'customers': {'5448': {'id': '5448', 'unr': '17626250', 'position': '3', 'name': 'Elia', 'pid': '1', 'img': {'color': 1, 'body': 7, 'face': 2, 'hair': 7, 'eyes': 6, 'mouth': 5, 'nose': 4}, 'slot': '0', 'grid': None, 'reward_multi': {'money': 1.19, 'points': 1.07, 'stonemason_points': 1.2}, 'reward': '', 'createdate': '1794196005', 'finishdate': '0', 'remain': -46378}, '5449': {'id': '5449', 'unr': '17626250', 'position': '4', 'name': 'Maxi', 'pid': '1', 'img': {'color': 3, 'body': 8, 'face': 4, 'hair': 10, 'eyes': 3, 'mouth': 7, 'nose': 1}, 'slot': '0', 'grid': None, 'reward_multi': {'money': 0.85, 'points': 0.9, 'stonemason_points': 1.11}, 'reward': '', 'createdate': '1794196005', 'finishdate': '0', 'remain': -46378}, '5597': {'id': '5597', 'unr': '17626250', 'position': '1', 'name': 'Kennedy', 'pid': '1', 'img': {'color': 4, 'body': 2, 'face': 4, 'hair': 6, 'eyes': 2, 'mouth': 2, 'nose': 1}, 'slot': '0', 'grid': None, 'reward_multi': {'money': 1.05, 'points': 0.97, 'stonemason_points': 1.19}, 'reward': '', 'createdate': '1794239103', 'finishdate': '0', 'remain': -3280}, '5613': {'id': '5613', 'unr': '17626250', 'position': '2', 'name': 'Loris', 'pid': '1', 'img': {'color': 2, 'body': 2, 'face': 2, 'hair': 1, 'eyes': 7, 'mouth': 5, 'nose': 5}, 'slot': '0', 'grid': None, 'reward_multi': {'money': 0.84, 'points': 1.02, 'stonemason_points': 0.84}, 'reward': '', 'createdate': '1794242736', 'finishdate': '0', 'remain': 353}}, 'level_percent': 0, 'level_rest': 100, 'exchangecoins': 2, 'exchangeamount': 25, 'stats': {'buy_tool1': '10', 'buy_tools': '10', 'customer_served': '2', 'customer_served_perfect': '2', 'product1_perfect': '2', 'tiles_removed': '4', 'tool1_used': '4', 'tools_used': '4'}, 'achievements': []}, 'config': {'constants': {'buy_leveladd_coins': 2, 'customerimg': {'color': 4, 'body': 9, 'face': 4, 'hair': 10, 'eyes': 7, 'mouth': 9, 'nose': 5}, 'speedup_tile': 600, 'customer_cooldown': 600, 'customer_speedup': 5000, 'tools_init': {'1': 5}, 'reward_multi': {'min': 0.8, 'max': 1.2}}, 'levels': {'1': {'points': 0, 'points_base': 25}, '2': {'points': 100, 'points_base': 50}, '3': {'points': 750, 'points_base': 200}, '4': {'points': 1500, 'points_base': 300}, '5': {'points': 6000, 'points_base': 500}, '6': {'points': 15000, 'points_base': 2000}, '7': {'points': 27000, 'points_base': 5000}, '8': {'points': 53000, 'points_base': 10000}, '9': {'points': 84000, 'points_base': 20000}, '10': {'points': 188000, 'points_base': 20000}, '11': {'points': 300000, 'points_base': 20000}, '12': {'points': 556000, 'points_base': 20000}, '13': {'points': 853000, 'points_base': 20000}, '14': {'points': 1555000, 'points_base': 20000}, '15': {'points': 2338000, 'points_base': 20000}, '16': {'points': 4210000, 'points_base': 20000}, '17': {'points': 6082000, 'points_base': 20000}, '18': {'points': 9457000, 'points_base': 20000}, '19': {'points': 13282000, 'points_base': 20000}, '20': {'points': 18382000, 'points_base': 20000}}, 'slots': {'1': {'level': 1}, '2': {'level': 5}, '3': {'level': 10}, '4': {'level': 15}}, 'customers': {'1': [], '2': [], '3': [], '4': []}, 'tools': {'1': {'name': 'Meißel', 'x': 1, 'y': 1, 'duration': 1800, 'money': {'100': 1, '200': 2, '500': 10}}, '2': {'name': 'Langer Meißel', 'x': 2, 'y': 1, 'duration': 2700, 'money': {'200': 1, '400': 2, '1000': 10}}, '3': {'name': 'Dünner Meißel', 'x': 1, 'y': 2, 'duration': 2700, 'money': {'200': 1, '400': 2, '1000': 10}}, '4': {'name': 'Großer Meißel', 'x': 2, 'y': 2, 'duration': 3200, 'money': {'300': 1, '600': 2, '1500': 10}}, '5': {'name': 'Riesenmeißel', 'x': 3, 'y': 3, 'duration': 1800, 'coins': {'2': 1, '4': 2, '20': 10}}, '6': {'name': 'Kreuzmeißel', 'form': '+', 'duration': 1800, 'coins': {'1': 1, '2': 2, '10': 10}}, '7': {'name': 'Rundmeißel', 'form': 'o', 'duration': 1800, 'coins': {'4': 1, '8': 2, '40': 10}}}, 'products': {'1': {'name': 'Sandstein-Ziegel', 'material': {'sandstone': 3}, 'level': 1, 'x': 4, 'y': 4, 'allowed': [1, 16], 'reward': {'money': 714, 'points': 833, 'stonemason_points': 10}}, '2': {'name': 'Granitziegel', 'material': {'granite': 5}, 'level': 2, 'x': 5, 'y': 5, 'allowed': [1, 2, 6, 20, 21, 24, 25], 'reward': {'money': 2778, 'points': 794, 'stonemason_points': 25}}, '3': {'name': 'Ziegelmauerstein', 'material': {'sandstone': 10}, 'level': 3, 'x': 6, 'y': 6, 'allowed': [1, 2, 3, 6, 7, 8, 24, 29, 30, 31, 34, 35, 36], 'reward': {'money': 3174, 'points': 3968, 'stonemason_points': 50}}, '4': {'name': 'Granitpflasterstein', 'material': {'granite': 10}, 'level': 4, 'x': 6, 'y': 6, 'allowed': [1, 2, 3, 5, 6, 7, 8, 23, 24, 25, 28, 29, 30, 31, 34, 35, 36], 'reward': {'money': 9243, 'points': 2521, 'stonemason_points': 100}}, '5': {'name': 'Ziegelskulptur', 'material': {'sandstone': 15}, 'level': 5, 'x': 7, 'y': 7, 'allowed': [1, 2, 3, 4, 7, 8, 9, 10, 27, 28, 29, 30, 34, 35, 36, 42, 43, 44, 48, 49], 'reward': {'money': 5952, 'points': 9523, 'stonemason_points': 200}}, '6': {'name': 'Granitstatue', 'material': {'granite': 15}, 'level': 6, 'x': 7, 'y': 7, 'allowed': [1, 2, 3, 6, 7, 8, 9, 13, 14, 15, 16, 20, 21, 22, 28, 29, 30, 34, 35, 36, 37, 41, 42, 43, 44, 48, 49], 'reward': {'money': 15476, 'points': 4167, 'stonemason_points': 400}}, '7': {'name': 'Ziegelofen', 'material': {'sandstone': 20}, 'level': 8, 'x': 8, 'y': 8, 'allowed': [1, 2, 7, 8, 9, 10, 15, 16, 17, 24, 33, 41, 44, 45, 48, 49, 52, 53, 56, 57, 64], 'reward': {'money': 11111, 'points': 15873, 'stonemason_points': 800}}, '8': {'name': 'Granitbrunnen', 'material': {'granite': 20}, 'level': 10, 'x': 8, 'y': 8, 'allowed': [1, 2, 3, 6, 7, 8, 9, 16, 17, 18, 23, 24, 25, 32, 33, 34, 39, 40, 41, 42, 43, 46, 47, 48, 49, 50, 55, 56, 57, 58, 63, 64], 'reward': {'money': 23809, 'points': 6349, 'stonemason_points': 1500}}, '9': {'name': 'Ziegelbogen', 'material': {'sandstone': 35}, 'level': 12, 'x': 9, 'y': 9, 'allowed': [1, 2, 3, 4, 7, 8, 9, 10, 11, 12, 19, 20, 28, 37, 43, 45, 46, 52, 53, 54, 61, 62, 63, 64, 69, 70, 71, 72, 73, 74, 75, 77, 78, 79, 80, 81], 'reward': {'money': 22857, 'points': 34285, 'stonemason_points': 3000}}, '10': {'name': 'Granit-Säule', 'material': {'granite': 40}, 'level': 14, 'x': 9, 'y': 9, 'allowed': [1, 2, 3, 7, 8, 9, 10, 11, 12, 16, 17, 18, 19, 20, 21, 22, 24, 25, 26, 27, 28, 29, 30, 31, 33, 34, 35, 36, 37, 38, 39, 40, 42, 43, 44, 45, 46, 47, 48, 52, 53, 54, 55, 56, 57, 62, 63, 64, 65, 66, 71, 72, 73, 74, 75, 76, 79, 80, 81], 'reward': {'money': 60714, 'points': 16071, 'stonemason_points': 6000}}, '11': {'name': 'Grabstein', 'material': {'sandstone': 45}, 'level': 16, 'x': 9, 'y': 9, 'allowed': [1, 2, 3, 7, 8, 9, 10, 11, 12, 17, 18, 19, 20, 26, 27, 28, 29, 35, 36, 37, 38, 44, 45, 46, 47, 53, 54, 55, 56, 62, 63, 64, 65, 71, 72, 73, 81], 'reward': {'money': 35714, 'points': 48214, 'stonemason_points': 10000}}, '12': {'name': 'Granitdenkmal', 'material': {'granite': 45}, 'level': 18, 'x': 10, 'y': 10, 'allowed': [1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 17, 18, 19, 20, 21, 22, 23, 24, 28, 29, 30, 31, 32, 33, 34, 38, 39, 40, 41, 42, 43, 48, 49, 50, 51, 52, 59, 60, 61, 62, 63, 68, 69, 70, 71, 72, 73, 79, 80, 81, 82, 83, 89, 90, 91, 92, 93, 94, 98, 99, 100], 'reward': {'money': 71428, 'points': 17857, 'stonemason_points': 15000}}}, 'stars': {'1': {'points': 5, 'percent': 0.025}, '2': {'points': 25, 'percent': 0.05}, '3': {'points': 100, 'percent': 0.075}, '4': {'points': 250, 'percent': 0.1}, '5': {'points': 1000, 'percent': 0.2}}, 'achievements': {'customer_served': {'10': 1744, '100': 1745, '1000': 1746}, 'customer_served_perfect': {'10': 1747, '100': 1748}}}}, 'host': 'web10'}}

    dummy_client.api_call = AsyncMock(return_value=mock_sm_init)
    service = CraftingVillageService(client=dummy_client)
    snapshot = await service.init_remote()

    assert snapshot is not None
    assert snapshot.stonemason is not None
    sm = snapshot.stonemason

    # Stonemason status & progress
    assert sm.level == 1
    assert sm.points == 0
    assert sm.level_percent == 0.0
    assert sm.level_rest == 100
    assert sm.tools["1"] == 11

    # Customers queue
    assert len(sm.customers) == 4
    assert "5448" in sm.customers
    elia = sm.customers["5448"]
    assert elia.name == "Elia"
    assert elia.pid == "1"
    assert elia.is_waiting is True
    assert elia.is_assigned is False
    assert elia.calculate_progress_percent(2) == 0.0

    # Configs
    assert len(sm.slots_config) == 4
    assert len(sm.tools_config) >= 7
    assert sm.tools_config["1"].costs_coins is False
    assert sm.tools_config["5"].costs_coins is True

    assert len(sm.products_config) >= 12
    p1 = sm.products_config["1"]
    assert p1.name == "Sandstein-Ziegel"
    assert p1.material == {"sandstone": 3}
    assert p1.allowed == [1, 16]

    # Summary
    summary = service.get_summary()
    assert summary.stonemason_level == 1
    assert summary.stonemason_customers_count == 4
    assert summary.stonemason_active_slots_count == 0
