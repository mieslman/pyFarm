from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import create_access_token
from app.core.client import MFFGameClient
from app.main import app
from app.modules.insecthotel.service import InsectHotelService
from app.worker.scheduler import worker_scheduler


@pytest.fixture
def dummy_client() -> MFFGameClient:
    client = MFFGameClient(server=1, username="testuser", password="testpassword")
    client.rid = "mock_rid_insecthotel"
    return client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    token = create_access_token(data={"sub": "mff", "role": "admin"})
    return {"Authorization": f"Bearer {token}"}


class MockStockService:
    def __init__(self, stock_dict: dict[int, int]):
        self.main_stock = stock_dict

    def get_stock(self, pid: int) -> int:
        return self.main_stock.get(pid, 0)

    def deduct_stock(self, pid: int, amount: int, farm_id: int | None = None):
        if pid in self.main_stock:
            self.main_stock[pid] = max(0, self.main_stock[pid] - amount)

    async def grasp_products(self, requirements: list[dict[str, int]]) -> bool:
        for req in requirements:
            pid = int(req["pid"])
            amt = int(req["amount"])
            self.main_stock[pid] = self.main_stock.get(pid, 0) + amt + 500
        return True

    def get_product(self, pid: int):
        class P:
            def __init__(self, pid):
                self.name = f"Pflanze {pid}"

        return P(pid)


MOCK_INSECTHOTEL_INIT = {
    "datablock": 1,
    "updateblock": {
        "map": {
            "insecthotel": {
                "data": {
                    "id": "154",
                    "slots": {
                        "1": {"level": 6, "population": 778, "happiness": 15.0},
                        "2": {"level": 6, "population": 816, "happiness": 0.0},
                    },
                    "stock": {
                        "1": {"level": 2, "pid": 19, "amount": 20},  # Cap 120 -> missing 100 (>20%)
                        "2": {
                            "level": 2,
                            "pid": 20,
                            "amount": 110,
                        },  # Cap 120 -> missing 10 (<=20%)
                    },
                    "checkout": {
                        "level": 4,
                        "money": 10000.0,  # Limit: 17500 -> 57% (>50%)
                        "points": 50000,  # Limit: 350000
                    },
                },
                "config": {
                    "slots": {
                        "1": {"name": "Wildbienen"},
                        "2": {"name": "Ohrwürmer"},
                    },
                    "stock_level": {
                        "2": {"capacity": 120},
                    },
                    "checkout_level": {
                        "4": {
                            "limit": {
                                "money": 17500.0,
                                "points": 350000,
                            }
                        }
                    },
                },
            }
        }
    },
}


# ---------------------------------------------------------------------------
# 1. Parsing & Snapshot Initialization
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_insecthotel_init_parsing(
    dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch
):
    service = InsectHotelService(dummy_client)

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        if params.get("mode") == "insecthotel_init":
            return MOCK_INSECTHOTEL_INIT
        return {}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    snapshot = await service.init_remote()
    assert snapshot is not None
    assert snapshot.id == "154"
    assert snapshot.total_population == 778 + 816
    assert len(snapshot.slots) == 2
    assert snapshot.slots["1"].name == "Wildbienen"
    assert snapshot.slots["1"].population == 778

    assert len(snapshot.stock_slots) == 2
    assert snapshot.stock_slots["1"].pid == 19
    assert snapshot.stock_slots["1"].capacity == 120
    assert snapshot.stock_slots["1"].amount == 20

    assert snapshot.checkout.money == 10000.0
    assert snapshot.checkout.limit_money == 17500.0
    assert snapshot.checkout.fill_ratio_money > 0.5


# ---------------------------------------------------------------------------
# 2. Checkout Collection Threshold
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_insecthotel_collect_checkout_when_over_50_percent(
    dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch
):
    service = InsectHotelService(dummy_client)
    calls = []

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        calls.append((endpoint, params))
        if params.get("mode") == "insecthotel_init":
            return MOCK_INSECTHOTEL_INIT
        elif params.get("mode") == "insecthotel_collect_checkout":
            return {"datablock": 1}
        return {}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    await service.init_remote()
    collected = await service.collect_checkout()

    assert collected is True
    assert ("farm", {"mode": "insecthotel_collect_checkout"}) in calls
    assert service.snapshot.checkout.money == 0.0


@pytest.mark.asyncio
async def test_insecthotel_skip_checkout_when_under_threshold(
    dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch
):
    service = InsectHotelService(dummy_client)
    calls = []

    # Modify checkout to under 50%
    low_checkout_init = {
        "datablock": 1,
        "updateblock": {
            "map": {
                "insecthotel": {
                    "data": {
                        "id": "154",
                        "slots": {},
                        "stock": {},
                        "checkout": {"level": 4, "money": 2000.0, "points": 10000},
                    },
                    "config": {
                        "slots": {},
                        "stock_level": {},
                        "checkout_level": {"4": {"limit": {"money": 17500.0, "points": 350000}}},
                    },
                }
            }
        },
    }

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        calls.append((endpoint, params))
        if params.get("mode") == "insecthotel_init":
            return low_checkout_init
        return {}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    await service.init_remote()
    collected = await service.collect_checkout()

    assert collected is False
    assert ("farm", {"mode": "insecthotel_collect_checkout"}) not in calls


# ---------------------------------------------------------------------------
# 3. Stock Refill Logic & Reserve Protection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_insecthotel_refill_stock(
    dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch
):
    service = InsectHotelService(dummy_client)
    calls = []

    # Main warehouse has 200 units of PID 19 and 100 units of PID 20
    # Reserve buffer is 50 -> usable PID 19 is 200 - 50 = 150
    # Slot 1 is missing 100 units (> 20% capacity 120) -> refills 100 units!
    # Slot 2 is missing 10 units (<= 20% capacity 120) -> should NOT refill
    stock_mock = MockStockService({19: 200, 20: 100})

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        calls.append((endpoint, params))
        if params.get("mode") == "insecthotel_init":
            return MOCK_INSECTHOTEL_INIT
        elif params.get("mode") == "insecthotel_set_stockslot":
            return {"datablock": 1}
        return {}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    await service.init_remote(stock_service=stock_mock)
    refilled = await service.refill_stock(stock_service=stock_mock)

    assert refilled == 1
    assert (
        "farm",
        {"mode": "insecthotel_set_stockslot", "slot": "1", "pid": 19, "amount": 100},
    ) in calls
    # Slot 2 was not refilled
    assert not any(p.get("slot") == "2" for _, p in calls)
    # Warehouse stock decreased by 100
    assert stock_mock.get_stock(19) == 100


@pytest.mark.asyncio
async def test_insecthotel_refill_with_auto_buy_feed(
    dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch
):
    service = InsectHotelService(dummy_client)
    calls = []

    # Main warehouse has 0 units of PID 19 (empty)
    # Reserve buffer is 50 -> usable PID 19 without grasping would be 0
    # With auto_buy_feed=True, it grasps PID 19 and refills Slot 1!
    stock_mock = MockStockService({19: 0, 20: 100})

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        calls.append((endpoint, params))
        if params.get("mode") == "insecthotel_init":
            return MOCK_INSECTHOTEL_INIT
        elif params.get("mode") == "insecthotel_set_stockslot":
            return {"datablock": 1}
        return {}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    await service.init_remote(stock_service=stock_mock)
    refilled = await service.refill_stock(stock_service=stock_mock)

    assert refilled == 1
    assert (
        "farm",
        {"mode": "insecthotel_set_stockslot", "slot": "1", "pid": 19, "amount": 100},
    ) in calls


# ---------------------------------------------------------------------------
# 4. Service Serve Orchestration & Dynamic Rotation
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_insecthotel_serve(dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch):
    service = InsectHotelService(dummy_client)
    stock_mock = MockStockService({19: 500, 20: 500, 34: 500, 35: 500})
    calls = []

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        calls.append((endpoint, params))
        mode = params.get("mode")
        if mode == "insecthotel_init":
            return MOCK_INSECTHOTEL_INIT
        elif mode in (
            "insecthotel_collect_checkout",
            "insecthotel_set_stockslot",
            "insecthotel_delete_stockslot",
        ):
            return {"datablock": 1}
        return {}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    # Test serve with dynamic rotation enabled
    res = await service.serve(stock_service=stock_mock)
    assert res["active"] is True
    assert res["total_population"] == 778 + 816
    assert res["collected_checkout"] is True
    assert res["rotated_slots"] == 2
    assert res["refilled_slots"] == 2
    assert "Wildbienen" in res["endangered_species"]

    summary = service.get_summary()
    assert summary.active is True
    assert summary.hotel_id == "154"
    assert summary.strategy == "dynamic_rotation"


@pytest.mark.asyncio
async def test_insecthotel_dynamic_rotation_switch(
    dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch
):
    from app.modules.insecthotel.planner import plan_target_pids

    service = InsectHotelService(dummy_client)

    # 1. State where Wildbienen is endangered (< 20) -> should choose rescue set with Erdbeeren (20)
    async def mock_init_low(endpoint: str, params: dict[str, Any], **kwargs):
        return MOCK_INSECTHOTEL_INIT

    monkeypatch.setattr(dummy_client, "api_call", mock_init_low)
    snap_low = await service.init_remote()
    target_low = plan_target_pids(snap_low, max_slots=8)
    assert 20 in target_low  # Erdbeeren included to rescue Wildbienen!
    assert 24 not in target_low

    # 2. State where all populations are healthy (> 50) -> should choose triple growth set with Blumenkohl (24)
    healthy_init = {
        "datablock": 1,
        "updateblock": {
            "map": {
                "insecthotel": {
                    "data": {
                        "id": "154",
                        "slots": {
                            "1": {"level": 6, "population": 800, "happiness": 55.0},
                            "2": {"level": 6, "population": 1000, "happiness": 52.0},
                        },
                        "stock": {
                            "1": {"level": 2, "pid": 19, "amount": 100},
                            "2": {"level": 2, "pid": 20, "amount": 100},
                        },
                        "checkout": {"level": 4, "money": 0.0, "points": 0},
                    },
                    "config": {
                        "slots": {
                            "1": {
                                "name": "Wildbienen",
                                "population_limit": [20, 50],
                                "happiness_decay": 4,
                            },
                            "2": {
                                "name": "Ohrwürmer",
                                "population_limit": [25, 50],
                                "happiness_decay": 3,
                            },
                        },
                        "stock_level": {"2": {"capacity": 120}},
                        "checkout_level": {"4": {"limit": {"money": 17500.0, "points": 350000}}},
                    },
                }
            }
        },
    }

    async def mock_init_healthy(endpoint: str, params: dict[str, Any], **kwargs):
        return healthy_init

    monkeypatch.setattr(dummy_client, "api_call", mock_init_healthy)
    snap_healthy = await service.init_remote()
    target_healthy = plan_target_pids(snap_healthy, max_slots=8)
    assert (
        24 in target_healthy
    )  # Blumenkohl included for max growth of Schwebfliegen + Schmetterling!


# ---------------------------------------------------------------------------
# 5. REST API Endpoints
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_insecthotel_api(
    auth_headers: dict[str, str], dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch
):
    ih_service = InsectHotelService(dummy_client)

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        if params.get("mode") == "insecthotel_init":
            return MOCK_INSECTHOTEL_INIT
        return {"datablock": 1}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)
    await ih_service.init_remote()
    worker_scheduler.last_insecthotel_service = ih_service

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. GET /api/v1/insecthotel
        res = await client.get("/api/v1/insecthotel", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert "config" in data
        assert "summary" in data
        assert data["summary"]["active"] is True
        assert "strategy" in data["config"]

        # 2. GET /api/v1/insecthotel/settings
        res = await client.get("/api/v1/insecthotel/settings", headers=auth_headers)
        assert res.status_code == 200
        assert res.json().get("enabled") is True

        # 3. PUT /api/v1/insecthotel/settings
        res = await client.put(
            "/api/v1/insecthotel/settings",
            json={
                "checkout_threshold_percent": 0.75,
                "min_stock_reserve": 100,
                "strategy": "dynamic_rotation",
                "auto_rotate_slots": True,
            },
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["config"]["checkout_threshold_percent"] == 0.75
        assert res.json()["config"]["min_stock_reserve"] == 100
        assert res.json()["config"]["strategy"] == "dynamic_rotation"

        # 4. POST /api/v1/insecthotel/action/checkout
        res = await client.post(
            "/api/v1/insecthotel/action/checkout?force=true", headers=auth_headers
        )
        assert res.status_code == 200
        assert "collected" in res.json()

        # 5. POST /api/v1/insecthotel/action/rotate
        res = await client.post(
            "/api/v1/insecthotel/action/rotate?force=true", headers=auth_headers
        )
        assert res.status_code == 200
        assert "result" in res.json()

        # 6. POST /api/v1/insecthotel/action/refill
        res = await client.post(
            "/api/v1/insecthotel/action/refill?force=true", headers=auth_headers
        )
        assert res.status_code == 200
        assert "refilled_slots" in res.json()


def test_planner_mathematical_guarantees():
    from app.modules.insecthotel.planner import (
        RESCUE_TWIN_GROWTH_PIDS,
        TRIPLE_GROWTH_PIDS,
        calculate_species_net_happiness,
    )

    # 1. TRIPLE_GROWTH_PIDS: Schmetterling, Marienkäfer, and Schwebfliegen MUST all be positive!
    triple_set = set(TRIPLE_GROWTH_PIDS)
    assert calculate_species_net_happiness("6", triple_set) == 1.75  # Schmetterling (+1.75)
    assert calculate_species_net_happiness("4", triple_set) == 0.75  # Marienkäfer (+0.75)
    assert calculate_species_net_happiness("3", triple_set) == 0.50  # Schwebfliegen (+0.50)

    # 2. RESCUE_TWIN_GROWTH_PIDS: Wildbienen MUST be positive (+0.4) and Ohrwürmer neutral (0.0)!
    rescue_set = set(RESCUE_TWIN_GROWTH_PIDS)
    assert (
        calculate_species_net_happiness("1", rescue_set) == 0.40
    )  # Wildbienen (+0.40) -> rescues bees!
    assert calculate_species_net_happiness("2", rescue_set) == 0.00  # Ohrwürmer (0.00) -> 0 loss!
    assert (
        calculate_species_net_happiness("6", rescue_set) == 1.25
    )  # Schmetterling (+1.25) -> fast growth!
    assert (
        calculate_species_net_happiness("4", rescue_set) == 0.75
    )  # Marienkäfer (+0.75) -> growth!
