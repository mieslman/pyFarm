from typing import Any
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.core.auth import create_access_token
from app.core.client import MFFGameClient
from app.main import app
from app.modules.events.calendar import CalendarEventService
from app.modules.events.delivery import DeliveryEventService
from app.modules.events.eventgarden import EventGardenService
from app.modules.events.manager import EventManager
from app.modules.events.oktoberfest import OktoberfestEventService
from app.modules.events.olympia import OlympiaEventService
from app.modules.events.pentecost import PentecostEventService


@pytest.fixture
def dummy_client() -> MFFGameClient:
    client = MFFGameClient(server=1, username="testuser", password="testpassword")
    client.rid = "mock_rid_events"
    return client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    token = create_access_token(data={"sub": "mff", "role": "admin"})
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. Event Detection from HTML
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_event_manager_detect_active_events(dummy_client: MFFGameClient):
    mgr = EventManager(dummy_client)
    html = """
    <html>
    <head><script>
    var events = {};
    events.data = [
        {"name": "calendar", "title": "Adventskalender"},
        {"name": "oktoberfest", "title": "Wiesn Gaudi"},
        {"name": "deliveryevent", "title": "Erntedank-Tour"}
    ];
    var other_var = 123;
    </script></head>
    </html>
    """
    detected = await mgr.detect_active_events(html_content=html)
    assert detected == ["calendar", "oktoberfest", "deliveryevent"]


@pytest.mark.asyncio
async def test_event_manager_detect_none_when_empty(dummy_client: MFFGameClient):
    mgr = EventManager(dummy_client)
    html = "<html><body>Keine Events</body></html>"
    detected = await mgr.detect_active_events(html_content=html)
    assert detected == []


# ---------------------------------------------------------------------------
# 2. Calendar Event Service
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_calendar_service_opens_door(dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch):
    service = CalendarEventService(dummy_client)

    calls = []

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        calls.append((endpoint, params))
        mode = params.get("mode")
        if mode == "calendar_init":
            return {
                "datablock": {
                    "day": 14,
                    "config": {"fields": {"14": {"reward": "gift"}}},
                    "data": {"days": {"13": 1700000000}},  # Day 14 not yet opened
                }
            }
        elif mode == "calendar_openfield":
            return {"datablock": {"day": 14, "reward": "points"}}
        return {}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    success = await service.serve()
    assert success is True
    assert ("farm", {"mode": "calendar_openfield", "field": 14, "day": 1}) in calls

    status = service.last_status
    assert status is not None
    assert status.day == 14


@pytest.mark.asyncio
async def test_calendar_service_already_opened(dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch):
    service = CalendarEventService(dummy_client)

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        return {
            "datablock": {
                "day": 14,
                "config": {"fields": {"14": {}}},
                "data": {"days": {"14": 1700000000}},  # Already opened
            }
        }

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    success = await service.serve()
    assert success is False


# ---------------------------------------------------------------------------
# 3. Delivery Event Service
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delivery_service_optimizes_tour(dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch):
    service = DeliveryEventService(dummy_client)
    calls = []

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        calls.append((endpoint, params))
        if params.get("mode") == "deliveryevent_init":
            return {
                "datablock": {
                    "data": {"points": 150, "tour": {"remain": -1}},
                    "config": {
                        "spots": {
                            "1": {"name": "Spot 1", "points": 100, "duration": 1000},  # outcome = 0.10
                            "2": {"name": "Spot 2", "points": 300, "duration": 1000},  # outcome = 0.30 (too expensive)
                            "3": {"name": "Spot 3", "points": 50, "duration": 1000},   # outcome = 0.05
                        }
                    },
                }
            }
        elif params.get("mode") == "deliveryevent_starttour":
            return {"datablock": {"status": 1}}
        return {}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    success = await service.serve()
    assert success is True
    # Should start Spot 1 since Spot 2 requires 300 points (we have 150) and Spot 1 has better outcome than Spot 3
    assert ("farm", {"mode": "deliveryevent_starttour", "spot": "1"}) in calls


@pytest.mark.asyncio
async def test_delivery_service_skips_when_tour_active(dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch):
    service = DeliveryEventService(dummy_client)

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        return {
            "datablock": {
                "data": {"points": 500, "tour": {"remain": 360, "spot": "1"}},
                "config": {"spots": {"1": {"points": 100, "duration": 1000}}},
            }
        }

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)
    success = await service.serve()
    assert success is False


# ---------------------------------------------------------------------------
# 4. Event Garden Service
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_eventgarden_harvest_and_plant(dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch):
    service = EventGardenService(dummy_client)
    calls = []

    state = {
        "tiles": {"1": {"remain": 0}, "2": {"remain": 0}},  # All ripe
        "stock": {"901": 20, "902": 150},
        "products": {"901": {"name": "Kürbis"}, "902": {"name": "Eventblume"}},
    }

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        calls.append((endpoint, params))
        mode = params.get("mode")
        if mode == "eventgarden_init":
            return {
                "datablock": {
                    "data": {"tiles": state["tiles"], "stock": state["stock"]},
                    "config": {"products": state["products"]},
                }
            }
        elif mode == "eventgarden_harvest_all":
            state["tiles"] = {}  # Empty after harvest
            return {
                "datablock": {
                    "data": {"tiles": {}, "stock": state["stock"]},
                    "config": {"products": state["products"]},
                }
            }
        elif mode == "eventgarden_autoplant":
            plant_id = params.get("plant")
            state["tiles"] = {"1": {"remain": 1200, "plant": plant_id}}
            return {"datablock": {"data": {"status": "ok"}}}
        return {}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    success = await service.serve()
    assert success is True
    # Harvest was called
    assert ("farm", {"mode": "eventgarden_harvest_all"}) in calls
    # Autoplant was called with highest stock PID (902, amount 150)
    assert ("farm", {"mode": "eventgarden_autoplant", "plant": 902}) in calls


# ---------------------------------------------------------------------------
# 5. Oktoberfest Backtracking Solver & Service
# ---------------------------------------------------------------------------
def test_oktoberfest_solver_pure_logic():
    # 4 sheep, 2 seats per bench (half = 2)
    # Seat 0, 1 on top bench; Seat 2, 3 on bottom bench
    sheeps = {
        "1": {"interests": ["beer", "pretzel"]},
        "2": {"interests": ["pretzel", "music"]},
        "3": {"interests": ["beer", "gingerbread"]},
        "4": {"interests": ["music", "gingerbread"]},
    }

    solution = OktoberfestEventService.solve_seating(sheeps)
    assert solution is not None
    assert len(solution) == 4

    # Verify seating constraints:
    # Seat 1 next to 0
    assert OktoberfestEventService.have_common_interest(solution[0], solution[1])
    # Seat 3 next to 2
    assert OktoberfestEventService.have_common_interest(solution[2], solution[3])
    # Seat 2 opposite 0
    assert OktoberfestEventService.have_common_interest(solution[0], solution[2])
    # Seat 3 opposite 1
    assert OktoberfestEventService.have_common_interest(solution[1], solution[3])


@pytest.mark.asyncio
async def test_oktoberfest_service_executes_rounds(dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch):
    service = OktoberfestEventService(dummy_client)
    calls = []

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        calls.append((endpoint, params))
        mode = params.get("mode")
        if mode == "oktoberfest_init":
            return {
                "datablock": {
                    "data": {
                        "cooldown_remain": 0,
                        "sheeps": {
                            "10": {"interests": ["a", "b"]},
                            "20": {"interests": ["b", "c"]},
                            "30": {"interests": ["a", "d"]},
                            "40": {"interests": ["c", "d"]},
                        },
                    }
                }
            }
        elif mode in ("oktoberfest_set_sheep", "oktoberfest_finish"):
            return {"datablock": {"data": {"success": 1}}}
        return {}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    success = await service.serve()
    assert success is True
    # Checked that 4 sheep were placed and finish was called
    sheep_calls = [p for endp, p in calls if p.get("mode") == "oktoberfest_set_sheep"]
    assert len(sheep_calls) == 4
    finish_calls = [p for endp, p in calls if p.get("mode") == "oktoberfest_finish"]
    assert len(finish_calls) == 1


# ---------------------------------------------------------------------------
# 6. Pentecost & Olympia Services
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pentecost_service(dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch):
    service = PentecostEventService(dummy_client)
    calls = []

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        calls.append((endpoint, params))
        mode = params.get("mode")
        if mode == "pentecostevent_init":
            return {
                "datablock": {
                    "config": {
                        "exchange": {
                            "water": {"amount": 50},
                            "fertilizer": {"amount": 30},
                        }
                    },
                    "data": {
                        "water": 100,
                        "fertilizer": 20,  # Not enough fertilizer
                        "water_remain": -10,  # Ready to water
                        "fertilizer_remain": 500,  # Cooldown active
                    },
                }
            }
        elif mode == "pentecostevent_care":
            return {"datablock": {"status": "ok"}}
        return {}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    success = await service.serve()
    assert success is True
    # Water care performed, fertilizer skipped
    assert ("farm", {"mode": "pentecostevent_care", "type": "water"}) in calls
    assert ("farm", {"mode": "pentecostevent_care", "type": "fertilizer"}) not in calls


@pytest.mark.asyncio
async def test_olympia_service(dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch):
    service = OlympiaEventService(dummy_client)
    calls = []

    async def mock_api_call(endpoint: str, params: dict[str, Any], **kwargs):
        calls.append((endpoint, params))
        action = params.get("action")
        if action == "olympia_init":
            return {
                "datablock": {
                    "energy": 70,
                    "data": {
                        "berries": 1000,  # Needed: (100 - 70) * 20 = 600 <= 1000
                    },
                }
            }
        elif action == "olympia_entry":
            return {"datablock": {"status": 1}}
        return {}

    monkeypatch.setattr(dummy_client, "api_call", mock_api_call)

    success = await service.serve()
    assert success is True
    assert ("main", {"action": "olympia_entry", "amount": 10}) in calls


# ---------------------------------------------------------------------------
# 7. Event Manager Orchestration
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_event_manager_serve_orchestration(dummy_client: MFFGameClient, monkeypatch: pytest.MonkeyPatch):
    mgr = EventManager(dummy_client)

    calendar_called = []
    oktoberfest_called = []

    async def mock_cal_serve():
        calendar_called.append(True)
        return True

    async def mock_okt_serve():
        oktoberfest_called.append(True)
        return True

    monkeypatch.setattr(mgr.calendar, "serve", mock_cal_serve)
    monkeypatch.setattr(mgr.oktoberfest, "serve", mock_okt_serve)

    # Simulate detecting calendar and oktoberfest
    html = 'events.data = [{"name": "calendar"}, {"name": "oktoberfest"}];'
    results = await mgr.serve(html_content=html)

    assert results == {"calendar": True, "oktoberfest": True}
    assert len(calendar_called) == 1
    assert len(oktoberfest_called) == 1


# ---------------------------------------------------------------------------
# 8. REST-API Endpoints
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_events_api_endpoints(auth_headers: dict[str, str]):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. GET /api/v1/events
        res = await client.get("/api/v1/events", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert "config" in data
        assert "overview" in data

        # 2. GET /api/v1/events/settings
        res = await client.get("/api/v1/events/settings", headers=auth_headers)
        assert res.status_code == 200
        settings_data = res.json()
        assert settings_data.get("enabled") is True

        # 3. PUT /api/v1/events/settings
        res = await client.put(
            "/api/v1/events/settings",
            json={"oktoberfest_enabled": False, "calendar_enabled": True},
            headers=auth_headers,
        )
        assert res.status_code == 200
        assert res.json()["config"]["oktoberfest_enabled"] is False
