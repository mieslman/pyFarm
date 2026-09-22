from typing import Any
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
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
                        "2": {"level": 2, "pid": 20, "amount": 110}, # Cap 120 -> missing 10 (<=20%)
                    },
                    "checkout": {
                        "level": 4,
                        "money": 10000.0,  # Limit: 17500 -> 57% (>50%)
                        "points": 50000,   # Limit: 350000
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
async def test_insecthotel_init_parsing(dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch):
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
                        "checkout_level": {
                            "4": {"limit": {"money": 17500.0, "points": 350000}}
                        },
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
async def test_insecthotel_refill_stock(dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch):
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
# 4. Service Serve Orchestration
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_insecthotel_serve(dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch):
    service = InsectHotelService(dummy_client)
    stock_mock = MockStockService({19: 500, 20: 500})

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        mode = params.get("mode")
        if mode == "insecthotel_init":
            return MOCK_INSECTHOTEL_INIT
        elif mode in ("insecthotel_collect_checkout", "insecthotel_set_stockslot"):
            return {"datablock": 1}
        return {}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    res = await service.serve(stock_service=stock_mock)
    assert res["active"] is True
    assert res["total_population"] == 778 + 816
    assert res["collected_checkout"] is True
    assert res["refilled_slots"] == 1

    summary = service.get_summary()
    assert summary.active is True
    assert summary.hotel_id == "154"


# ---------------------------------------------------------------------------
# 5. REST API Endpoints
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_insecthotel_api(auth_headers: dict[str, str], dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch):
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

        # 2. GET /api/v1/insecthotel/settings
        res = await client.get("/api/v1/insecthotel/settings", headers=auth_headers)
        assert res.status_code == 200
        assert res.json().get("enabled") is True

        # 3. PUT /api/v1/insecthotel/settings
        res = await client.put(
            "/api/v1/insecthotel/settings",
            json={"checkout_threshold_percent": 0.75, "min_stock_reserve": 100},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["config"]["checkout_threshold_percent"] == 0.75
        assert res.json()["config"]["min_stock_reserve"] == 100

        # 4. POST /api/v1/insecthotel/action/checkout
        res = await client.post("/api/v1/insecthotel/action/checkout?force=true", headers=auth_headers)
        assert res.status_code == 200
        assert "collected" in res.json()

        # 5. POST /api/v1/insecthotel/action/refill
        res = await client.post("/api/v1/insecthotel/action/refill?force=true", headers=auth_headers)
        assert res.status_code == 200
        assert "refilled_slots" in res.json()
