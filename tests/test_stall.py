from typing import Any
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import create_access_token
from app.core.client import MFFGameClient
from app.main import app
from app.models.product import Product
from app.modules.stall.models import MarketStall, StallSlot
from app.modules.stall.service import FruitStallService
from app.worker.scheduler import worker_scheduler


@pytest.fixture
def auth_headers() -> dict[str, str]:
    token = create_access_token(data={"sub": "mff", "role": "admin"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def dummy_client() -> MFFGameClient:
    return MFFGameClient(server=1)


class MockStockForStall:
    def __init__(self, products_dict: dict[int, Product]):
        self.products = products_dict

    def get_stock(self, pid: int) -> int:
        prod = self.products.get(pid)
        return prod.amount if prod else 0

    def get_product(self, pid: int) -> Product | None:
        return self.products.get(pid)

    def deduct_stock(self, pid: int, amount: int, farm_id: int | None = None):
        if pid in self.products:
            self.products[pid].amount = max(0, self.products[pid].amount - amount)


MOCK_STALL_INIT = {
    "datablock": [1, 1],
    "updateblock": {
        "map": {
            "stall": {
                "data": {
                    "1": {
                        "position": 1,
                        "level": 11,
                        "points": 105600,
                        "farmi_count": 420,
                        "reward": {"money": 250.0, "points": 500},
                        "slots": {
                            "1": {"pid": 352, "amount": 80},  # Limette: 80 / 280 (< 50% = 140 -> should be cleared)
                            "2": {"pid": 354, "amount": 250}, # Papaya: 250 / 280 (>= 50% -> keep)
                        },
                    },
                    "2": {
                        "position": 2,
                        "level": 11,
                        "points": 80000,
                        "farmi_count": 310,
                        "reward": None,
                        "slots": {
                            "1": [],                           # Free slot -> should be filled
                            "2": {"time": 1661748302},         # Free slot -> should be filled
                        },
                    },
                },
                "config": {
                    "level": {
                        "1": {
                            "11": {"fillsum": 280},
                        },
                        "2": {
                            "11": {"fillsum": 280},
                        },
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
async def test_stall_init_parsing(dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch):
    service = FruitStallService(dummy_client)

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        if params.get("mode") == "stall_init":
            return MOCK_STALL_INIT
        return {}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    snapshot = await service.init_remote()
    assert snapshot is not None
    assert len(snapshot.stalls) == 2

    # Stall 1
    s1 = snapshot.stalls["1"]
    assert s1.position == "1"
    assert s1.level == 11
    assert s1.fillsum == 280
    assert s1.reward_ready is True
    assert len(s1.slots) == 2
    assert s1.slots["1"].pid == 352
    assert s1.slots["1"].amount == 80
    assert s1.slots["2"].pid == 354
    assert s1.slots["2"].amount == 250

    # Stall 2
    s2 = snapshot.stalls["2"]
    assert s2.position == "2"
    assert s2.reward_ready is False
    assert s2.slots["1"].is_empty is True
    assert s2.slots["2"].is_empty is True


# ---------------------------------------------------------------------------
# 2. Reward Collection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stall_collect_rewards(dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch):
    service = FruitStallService(dummy_client)
    calls = []

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        calls.append((endpoint, params))
        if params.get("mode") == "stall_init":
            return MOCK_STALL_INIT
        elif params.get("mode") == "stall_get_reward":
            return {"datablock": {"reward": 1}}
        return {}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    await service.init_remote()
    collected = await service.collect_rewards()

    assert collected == 1
    assert ("farm", {"mode": "stall_get_reward", "position": "1"}) in calls
    assert service.snapshot.stalls["1"].reward_ready is False


# ---------------------------------------------------------------------------
# 3. Clearing Depleted Slots
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stall_clear_depleted_slots(dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch):
    service = FruitStallService(dummy_client)
    calls = []

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        calls.append((endpoint, params))
        if params.get("mode") == "stall_init":
            return MOCK_STALL_INIT
        elif params.get("mode") == "stall_clear_slot":
            return {"datablock": 1}
        return {}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    await service.init_remote()
    cleared = await service.clear_depleted_slots()

    # Stall 1 Slot 1 has amount 80 < 280 * 0.5 (140) -> cleared!
    # Stall 1 Slot 2 has amount 250 >= 140 -> preserved!
    assert cleared == 1
    assert ("farm", {"mode": "stall_clear_slot", "position": "1", "slot": "1"}) in calls
    assert not any(p.get("slot") == "2" and p.get("mode") == "stall_clear_slot" for _, p in calls)
    assert service.snapshot.stalls["1"].slots["1"].is_empty is True


# ---------------------------------------------------------------------------
# 4. Slot Replenishment with Reserve Protection
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stall_fill_free_slots_with_reserve(dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch):
    service = FruitStallService(dummy_client)
    calls = []

    # Mock stock with exotic fruits (category 'ex')
    # Required per slot: fillsum = 280
    # Reserve buffer: min_stock_reserve = 500
    # Available usable:
    # - PID 351 (Ananas): amount 1200 -> usable = 1200 - 500 = 700 >= 280 (Valid)
    # - PID 353 (Litschi): amount 900 -> usable = 900 - 500 = 400 >= 280 (Valid)
    # - PID 355 (Maracuja): amount 650 -> usable = 650 - 500 = 150 < 280 (Too low, protected!)
    mock_stock = MockStockForStall({
        351: Product(pid=351, name="Ananas", price=10.0, category="ex", amount=1200),
        353: Product(pid=353, name="Litschi", price=12.0, category="ex", amount=900),
        355: Product(pid=355, name="Maracuja", price=15.0, category="ex", amount=650),
    })

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        calls.append((endpoint, params))
        if params.get("mode") == "stall_init":
            return MOCK_STALL_INIT
        elif params.get("mode") == "stall_fill_slot":
            return {"datablock": 1}
        return {}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    await service.init_remote(stock_service=mock_stock)
    # Stall 2 has 2 free slots ('1' and '2')
    filled = await service.fill_free_slots(stock_service=mock_stock)

    assert filled == 2
    # Chosen fruits should be 351 (highest usable) and 353
    assert ("farm", {"mode": "stall_fill_slot", "position": "2", "slot": "1", "pid": 351, "amount": 280}) in calls
    assert ("farm", {"mode": "stall_fill_slot", "position": "2", "slot": "2", "pid": 353, "amount": 280}) in calls
    # PID 355 was not used because usable (150) < 280
    assert not any(p.get("pid") == 355 for _, p in calls)
    # Stock deducted
    assert mock_stock.get_stock(351) == 920
    assert mock_stock.get_stock(353) == 620


# ---------------------------------------------------------------------------
# 5. Service Serve Orchestration
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stall_serve(dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch):
    service = FruitStallService(dummy_client)
    mock_stock = MockStockForStall({
        351: Product(pid=351, name="Ananas", price=10.0, category="ex", amount=1500),
        353: Product(pid=353, name="Litschi", price=12.0, category="ex", amount=1400),
        356: Product(pid=356, name="Banane", price=11.0, category="ex", amount=1300),
    })

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        mode = params.get("mode")
        if mode == "stall_init":
            return MOCK_STALL_INIT
        elif mode in ("stall_get_reward", "stall_clear_slot", "stall_fill_slot"):
            return {"datablock": 1}
        return {}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    result = await service.serve(stock_service=mock_stock)
    assert result["active"] is True
    assert result["stalls_count"] == 2
    assert result["rewards_collected"] == 1
    assert result["cleared_slots"] == 1
    # 2 free slots in Stall 2 + 1 cleared slot in Stall 1 = 3 slots filled
    assert result["filled_slots"] == 3


# ---------------------------------------------------------------------------
# 6. REST API Endpoints
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stall_api(dummy_client: MFFGameClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch):
    st_service = FruitStallService(dummy_client)
    mock_stock = MockStockForStall({
        351: Product(pid=351, name="Ananas", price=10.0, category="ex", amount=1500),
    })

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        mode = params.get("mode")
        if mode == "stall_init":
            return MOCK_STALL_INIT
        elif mode in ("stall_get_reward", "stall_clear_slot", "stall_fill_slot"):
            return {"datablock": 1}
        return {}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    await st_service.init_remote(stock_service=mock_stock)
    worker_scheduler.last_stall_service = st_service
    worker_scheduler.last_stock_service = mock_stock

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. GET /api/v1/stall
        res = await client.get("/api/v1/stall", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert "config" in data
        assert "summary" in data
        assert data["summary"]["active"] is True
        assert data["summary"]["stalls_count"] == 2

        # 2. GET /api/v1/stall/settings
        res = await client.get("/api/v1/stall/settings", headers=auth_headers)
        assert res.status_code == 200
        assert res.json().get("enabled") is True
        assert res.json().get("min_stock_reserve") == 500

        # 3. PUT /api/v1/stall/settings
        res = await client.put(
            "/api/v1/stall/settings",
            json={"clear_threshold_percent": 0.4, "min_stock_reserve": 600},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["config"]["clear_threshold_percent"] == 0.4
        assert res.json()["config"]["min_stock_reserve"] == 600

        # Reset back to 500 for tests
        await client.put(
            "/api/v1/stall/settings",
            json={"clear_threshold_percent": 0.5, "min_stock_reserve": 500},
            headers=auth_headers,
        )

        # 4. POST /api/v1/stall/action/collect
        res = await client.post("/api/v1/stall/action/collect", headers=auth_headers)
        assert res.status_code == 200
        assert "collected_rewards" in res.json()

        # 5. POST /api/v1/stall/action/clear
        res = await client.post("/api/v1/stall/action/clear", headers=auth_headers)
        assert res.status_code == 200
        assert "cleared_slots" in res.json()

        # 6. POST /api/v1/stall/action/fill
        res = await client.post("/api/v1/stall/action/fill", headers=auth_headers)
        assert res.status_code == 200
        assert "filled_slots" in res.json()
