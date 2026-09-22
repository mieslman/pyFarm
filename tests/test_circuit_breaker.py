"""Unit tests for Global API Circuit Breaker."""

import httpx
import pytest
import respx

from app.core.circuit_breaker import CircuitBreaker, circuit_breaker
from app.core.client import MFFGameClient
from app.core.exceptions import CircuitBreakerOpenError, UpstreamAPIError
from app.worker.scheduler import worker_scheduler


@pytest.mark.asyncio
async def test_circuit_breaker_normal_operation(fast_client: MFFGameClient):
    """Test that requests pass through and circuit breaker remains CLOSED when API works normally."""
    cb = CircuitBreaker()
    fast_client.circuit_breaker = cb
    fast_client.rid = "test_rid"

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": [1]})
        )

        res = await fast_client.api_call("farm", {"mode": "test"})
        assert res == {"datablock": [1]}
        assert cb.state == "CLOSED"
        assert cb.is_open is False
        assert cb.failure_count == 0


@pytest.mark.asyncio
async def test_circuit_breaker_trips_on_failed_plaintext(fast_client: MFFGameClient):
    """Test that when upstream returns plain text 'failed', circuit breaker trips to OPEN and halts further calls."""
    cb = CircuitBreaker()
    fast_client.circuit_breaker = cb
    fast_client.rid = "test_rid"

    with respx.mock:
        mock_route = respx.get(url__startswith="https://s1.myfreefarm.de/ajax/city.php").mock(
            return_value=httpx.Response(200, text="failed")
        )

        with pytest.raises(UpstreamAPIError):
            await fast_client.api_call("city", {"mode": "marketcreateoffer"})

        assert cb.state == "OPEN"
        assert cb.is_open is True
        assert "failed" in cb.trip_reason.lower()
        assert mock_route.call_count == 1

        # Subsequent call MUST be blocked by circuit breaker before any HTTP request is made
        with pytest.raises(CircuitBreakerOpenError):
            await fast_client.api_call("farm", {"mode": "getfarms"})

        # HTTP call count must still be 1! No new request was sent to the server.
        assert mock_route.call_count == 1


@pytest.mark.asyncio
async def test_circuit_breaker_trips_on_http_429(fast_client: MFFGameClient):
    """Test that HTTP 429 Too Many Requests trips the circuit breaker immediately."""
    cb = CircuitBreaker()
    fast_client.circuit_breaker = cb
    fast_client.rid = "test_rid"

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(429, text="Rate limit exceeded")
        )

        with pytest.raises(UpstreamAPIError):
            await fast_client.api_call("farm", {"mode": "getfarms"})

        assert cb.state == "OPEN"
        assert cb.is_open is True
        assert "429" in cb.trip_reason


@pytest.mark.asyncio
async def test_circuit_breaker_manual_reset():
    """Test resetting the circuit breaker restores normal CLOSED operation."""
    cb = CircuitBreaker()
    cb.trip("Security block")
    assert cb.is_open is True
    assert cb.state == "OPEN"

    cb.reset()
    assert cb.is_open is False
    assert cb.state == "CLOSED"
    assert cb.trip_reason is None


@pytest.mark.asyncio
async def test_scheduler_skips_cycle_when_circuit_open():
    """Test WorkerScheduler immediately skips full cycle if global circuit breaker is active."""
    circuit_breaker.trip("Simulated ban/rate-limit")
    try:
        assert circuit_breaker.is_open is True

        res = await worker_scheduler.run_cycle()
        assert res["status"] == "skipped"
        assert res["reason"] == "circuit_breaker_open"
        assert worker_scheduler.current_state == "PAUSED_CIRCUIT_BREAKER"
    finally:
        circuit_breaker.reset()
        worker_scheduler.current_state = "IDLE"
        worker_scheduler.last_error = None
