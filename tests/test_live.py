import pytest

from app.config import settings
from app.core.client import MFFGameClient


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_server_login_and_getfarms():
    """End-to-End Test gegen den echten MyFreeFarm Spielserver.

    Wird nur ausgeführt, wenn in der .env-Datei gültige Zugangsdaten
    für MFF_ACCOUNT__USERNAME und MFF_ACCOUNT__PASSWORD hinterlegt sind.
    Ausführung mit: uv run pytest -m live -s
    """
    account = settings.account
    if not account.username or not account.password or account.username in ("", "DeinBenutzername"):
        pytest.skip(
            "Keine Live-Zugangsdaten in .env hinterlegt. "
            "Kopiere .env.example nach .env und trage deine Zugangsdaten ein."
        )

    client = MFFGameClient(
        server=account.server,
        username=account.username,
        password=account.password,
    )

    try:
        # 1. Echten Login durchführen
        logged_in = await client.login()
        assert logged_in is True
        assert client.rid is not None
        assert len(client.rid) > 5

        # 2. Authentifizierten getfarms Call absetzen
        res = await client.api_call("farm", {"mode": "getfarms", "farm": 1, "position": 0})
        assert "datablock" in res
        assert res["datablock"][0] == 1

    finally:
        # 3. Sauber abmelden
        await client.logout()
        assert client.rid is None
