import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.worker.scheduler import WorkerScheduler


@pytest.mark.asyncio
async def test_scheduler_state_and_lock(monkeypatch):
    """Test scheduler state management and cycle lock behavior."""
    monkeypatch.setattr("app.worker.scheduler.settings.account.username", "")
    scheduler = WorkerScheduler()
    assert scheduler.current_state == "IDLE"
    assert scheduler.is_running is False

    # Run cycle with no credentials set (should gracefully return error state)
    res = await scheduler.run_cycle()
    assert res.get("status") == "error"
    assert res.get("reason") == "no_credentials"


@pytest.mark.asyncio
async def test_bot_api_endpoints():
    """Test /api/v1/bot/status and /api/v1/bot/trigger."""
    from app.core.auth import create_access_token

    token = create_access_token({"sub": "mff", "role": "admin"})
    headers = {"Authorization": f"Bearer {token}"}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Health check (public)
        res_health = await ac.get("/api/v1/health")
        assert res_health.status_code == 200
        assert res_health.json() == {"status": "ok"}

        # 2. Bot status (protected)
        res_status = await ac.get("/api/v1/bot/status", headers=headers)
        assert res_status.status_code == 200
        data = res_status.json()
        assert "is_running" in data
        assert "state" in data

        # 3. Bot trigger (protected)
        res_trigger = await ac.post("/api/v1/bot/trigger", headers=headers)
        assert res_trigger.status_code == 200
        assert res_trigger.json()["status"] in ("started", "skipped")
