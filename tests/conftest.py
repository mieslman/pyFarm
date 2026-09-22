import httpx
import pytest
import respx

from app.config import settings
from app.core.circuit_breaker import CircuitBreaker, circuit_breaker
from app.core.client import MFFGameClient


@pytest.fixture(autouse=True, scope="session")
def preserve_real_user_config():
    """Preserve real data/user_config.json from any API test writes during pytest sessions."""
    from app.core.settings_manager import settings_manager

    real_path = settings_manager.config_path
    original_bytes = real_path.read_bytes() if real_path.exists() else None
    try:
        yield
    finally:
        if original_bytes is not None:
            real_path.write_bytes(original_bytes)
            settings_manager.load_settings()


@pytest.fixture(autouse=True)
def reset_global_circuit_breaker():
    """Ensure global circuit breaker is reset before and after every test."""
    circuit_breaker.reset()
    yield
    circuit_breaker.reset()


@pytest.fixture
def test_settings():
    """Returns testing configuration overrides."""
    return settings.model_copy(update={"debug": True})


@pytest.fixture
def fast_client():
    """Returns an MFFGameClient instance with 0ms jitter and isolated circuit breaker for fast unit/mock testing."""
    return MFFGameClient(
        server=1,
        username="TestFarmer",
        password="ValidPassword123",
        min_jitter_ms=0,
        max_jitter_ms=0,
        circuit_breaker_instance=CircuitBreaker(),
    )


@pytest.fixture
def mock_game_api():
    """Standard respx mock context with successful login and gardeninit endpoints."""
    with respx.mock(assert_all_called=False) as mock:
        # Mock token request
        mock.post("https://www.myfreefarm.de/ajax/createtoken2.php").mock(
            return_value=httpx.Response(
                200, json=[1, "https://s1.myfreefarm.de/login.php?token=test_token_xyz"]
            )
        )

        # Mock login redirect
        mock.get("https://s1.myfreefarm.de/login.php?token=test_token_xyz").mock(
            return_value=httpx.Response(
                200, text="<html><script>var rid = 'initial_rid_12345';</script></html>"
            )
        )

        # Mock gardeninit call
        mock.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(
                200,
                json={
                    "datablock": [
                        1,
                        {
                            "1": {"phase": 4, "iswater": 1, "harvest": 17},
                            "2": {"phase": 1, "iswater": 0, "harvest": 17},
                        },
                    ],
                    "updateblock": {"stock": {"stock": {1: {}}, "tempstock": {}}},
                },
            )
        )

        # Mock logout
        mock.get("https://s1.myfreefarm.de/main.php?page=logout&logoutbutton=1").mock(
            return_value=httpx.Response(200, text="Logged out")
        )

        yield mock
