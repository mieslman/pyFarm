import httpx
import pytest
import respx

from app.core.exceptions import (
    AuthenticationError,
    CaptchaRequiredError,
    SessionExpiredError,
    UpstreamMaintenanceError,
)


@pytest.mark.asyncio
async def test_login_success(fast_client, mock_game_api):
    """Test standard 2-step login and RID extraction."""
    success = await fast_client.login()
    assert success is True
    assert fast_client.rid == "initial_rid_12345"


@pytest.mark.asyncio
async def test_login_invalid_credentials(fast_client):
    """Test handling of bad credentials response [0, 'Benutzername oder Passwort falsch']."""
    with respx.mock:
        respx.post("https://www.myfreefarm.de/ajax/createtoken2.php").mock(
            return_value=httpx.Response(200, json=[0, "Benutzername oder Passwort falsch"])
        )
        with pytest.raises(AuthenticationError, match="Login verweigert"):
            await fast_client.login()


@pytest.mark.asyncio
async def test_login_captcha_detected(fast_client):
    """Test detection of Captcha challenge."""
    with respx.mock:
        respx.post("https://www.myfreefarm.de/ajax/createtoken2.php").mock(
            return_value=httpx.Response(
                200, json=[0, "Sicherheitsüberprüfung: Bitte Captcha lösen."]
            )
        )
        with pytest.raises(CaptchaRequiredError, match="Captcha"):
            await fast_client.login()


@pytest.mark.asyncio
async def test_api_call_success(fast_client, mock_game_api):
    """Test authenticated API call with valid datablock."""
    await fast_client.login()
    data = await fast_client.api_call("farm", {"mode": "gardeninit", "farm": 1, "position": 1})
    assert data["datablock"][0] == 1
    assert data["datablock"][1]["1"]["phase"] == 4


@pytest.mark.asyncio
async def test_api_call_auto_relogin_on_expired_rid(fast_client):
    """Test transparent session recovery when upstream returns expired session error."""
    with respx.mock:
        # Initial login
        respx.post("https://www.myfreefarm.de/ajax/createtoken2.php").mock(
            side_effect=[
                # 1. First login
                httpx.Response(200, json=[1, "https://s1.myfreefarm.de/login.php?token=tok1"]),
                # 2. Second login (re-login)
                httpx.Response(200, json=[1, "https://s1.myfreefarm.de/login.php?token=tok2"]),
            ]
        )
        respx.get("https://s1.myfreefarm.de/login.php?token=tok1").mock(
            return_value=httpx.Response(200, text="<script>var rid = 'old_rid';</script>")
        )
        respx.get("https://s1.myfreefarm.de/login.php?token=tok2").mock(
            return_value=httpx.Response(200, text="<script>var rid = 'new_fresh_rid';</script>")
        )

        # Call with old_rid fails with session error
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php?rid=old_rid").mock(
            return_value=httpx.Response(
                200, json={"status": "error", "message": "session expired (login required)"}
            )
        )
        # Call with new_fresh_rid succeeds
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php?rid=new_fresh_rid").mock(
            return_value=httpx.Response(200, json={"datablock": [1, {"1": {"phase": 4}}]})
        )

        await fast_client.login()
        assert fast_client.rid == "old_rid"

        # Execute call: should automatically recover, re-login, and return valid data!
        result = await fast_client.api_call("farm", {"mode": "gardeninit"})
        assert fast_client.rid == "new_fresh_rid"
        assert result["datablock"][0] == 1


@pytest.mark.asyncio
async def test_api_call_relogin_exhausted(fast_client):
    """Test that SessionExpiredError is raised if auto-relogin is disabled or retries exhausted."""
    fast_client.rid = "invalid_expired_rid"
    with respx.mock:
        respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/farm.php?rid=invalid_expired_rid"
        ).mock(
            return_value=httpx.Response(200, json={"status": "error", "message": "session expired"})
        )
        with pytest.raises(SessionExpiredError):
            await fast_client.api_call("farm", {"mode": "gardeninit"}, auto_relogin=False)


@pytest.mark.asyncio
async def test_upstream_maintenance_mode(fast_client):
    """Test detection of upstream 503 maintenance mode."""
    with respx.mock:
        respx.post("https://www.myfreefarm.de/ajax/createtoken2.php").mock(
            return_value=httpx.Response(
                503,
                text="<html><body>Wegen Wartungsarbeiten vorübergehend nicht erreichbar.</body></html>",
            )
        )
        with pytest.raises(UpstreamMaintenanceError, match="Wartungsmodus"):
            await fast_client.login()


@pytest.mark.asyncio
async def test_client_logout(fast_client, mock_game_api):
    """Test clean logout and session disposal."""
    await fast_client.login()
    assert fast_client.rid is not None

    await fast_client.logout()
    assert fast_client.rid is None
    assert fast_client._client is None
