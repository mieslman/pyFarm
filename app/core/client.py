import argparse
import asyncio
import json
import random
import re
import sys
import time
from typing import Any

import httpx
from loguru import logger

from app.core.circuit_breaker import CircuitBreaker, circuit_breaker
from app.core.exceptions import (
    AuthenticationError,
    CaptchaRequiredError,
    MFFException,
    NetworkTimeoutError,
    SessionExpiredError,
    UpstreamAPIError,
    UpstreamMaintenanceError,
)
from app.core.logging import mask_sensitive_data, setup_logging


class MFFGameClient:
    """Async HTTP client communicating with upstream myfreefarm.de game servers.

    Supports:
    - 2-step login with automatic RID regex extraction.
    - Anti-detection request jitter (350ms - 900ms).
    - Masked debug logging for passwords and tokens.
    - Transparent session recovery / auto-relogin on SessionExpiredError.
    - Detection of maintenance mode and captchas.
    """

    def __init__(
        self,
        server: int,
        username: str = "",
        password: str = "",
        min_jitter_ms: int = 350,
        max_jitter_ms: int = 900,
        circuit_breaker_instance: CircuitBreaker | None = None,
    ):
        self.server = server
        self.username = username
        self.password = password
        self.min_jitter_ms = min_jitter_ms
        self.max_jitter_ms = max_jitter_ms
        self.circuit_breaker = circuit_breaker_instance or circuit_breaker
        self.rid: str | None = None
        self._client: httpx.AsyncClient | None = None
        self.headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "*/*",
            "Accept-Language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": f"https://s{self.server}.myfreefarm.de",
            "Referer": f"https://s{self.server}.myfreefarm.de/main.php",
        }

    async def __aenter__(self):
        self._get_or_create_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    def _get_or_create_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=self.headers,
                follow_redirects=True,
                timeout=httpx.Timeout(30.0, connect=10.0),
            )
        return self._client

    @property
    def client(self) -> httpx.AsyncClient:
        return self._get_or_create_client()

    async def login(self, username: str | None = None, password: str | None = None) -> bool:
        """Authenticate with www.myfreefarm.de and extract session RID."""
        self.circuit_breaker.check_state()
        user = username or self.username
        pwd = password or self.password

        token_url = f"https://www.myfreefarm.de/ajax/createtoken2.php?n={int(time.time())}"
        data = {
            "server": str(self.server),
            "username": user,
            "password": pwd,
            "ref": "frbek",
            "retid": "",
        }

        logger.debug(f"Anmelden bei Server {self.server}: {mask_sensitive_data(data)} ...")
        try:
            res = await self.client.post(token_url, data=data)
        except httpx.TimeoutException as e:
            self.circuit_breaker.record_failure("Timeout beim Login-Token-Abruf")
            raise NetworkTimeoutError(f"Timeout beim Login-Token-Abruf: {e}") from e

        if res.status_code == 503 or "wartung" in res.text.lower():
            raise UpstreamMaintenanceError(
                f"Server {self.server} ist im Wartungsmodus (HTTP {res.status_code})"
            )
        if res.status_code == 429:
            self.circuit_breaker.trip("HTTP 429 Too Many Requests beim Login")
            raise AuthenticationError("HTTP 429 Too Many Requests beim Login")
        if res.status_code != 200:
            self.circuit_breaker.record_failure(f"Login-Token HTTP {res.status_code}")
            raise AuthenticationError(f"Token request failed with status {res.status_code}")

        try:
            token_data = res.json()
        except json.JSONDecodeError:
            if "wartung" in res.text.lower():
                raise UpstreamMaintenanceError(f"Server {self.server} im Wartungsmodus.")
            raise AuthenticationError(f"Ungültige JSON-Antwort beim Login: {res.text[:200]}")

        # Upstream returns [1, "redirect_url"] on success or [0, "error message"] on failure
        if isinstance(token_data, list):
            if len(token_data) >= 1 and token_data[0] == 0:
                err_msg = token_data[1] if len(token_data) > 1 else "Unbekannter Login-Fehler"
                if "captcha" in str(err_msg).lower():
                    self.circuit_breaker.trip(f"Captcha-Sicherheitsabfrage erforderlich: {err_msg}")
                    raise CaptchaRequiredError(
                        f"Captcha-Sicherheitsabfrage erforderlich: {err_msg}"
                    )
                if "support" in str(err_msg).lower() or "gesperrt" in str(err_msg).lower():
                    self.circuit_breaker.trip(f"Upjers Login verweigert: {err_msg}")
                raise AuthenticationError(f"Login verweigert: {err_msg}")

            if len(token_data) < 2 or not token_data[0]:
                raise AuthenticationError(f"Unerwartete Login-Antwort: {token_data}")

            redirect_url = token_data[1]
        elif isinstance(token_data, dict) and token_data.get("status") == "error":
            raise AuthenticationError(f"Login fehlgeschlagen: {token_data.get('message')}")
        else:
            raise AuthenticationError(f"Unerwartetes Login-Format: {token_data}")

        logger.debug(f"Folge Login-Redirect: {redirect_url}")
        try:
            page_res = await self.client.get(redirect_url)
        except httpx.TimeoutException as e:
            self.circuit_breaker.record_failure("Timeout beim Abrufen der Redirect-Seite")
            raise NetworkTimeoutError(f"Timeout beim Abrufen der Redirect-Seite: {e}") from e

        if page_res.status_code == 503:
            raise UpstreamMaintenanceError(f"Server {self.server} ist im Wartungsmodus (HTTP 503).")

        # Extract RID: var rid = 'xyz';
        match = re.search(r"var rid\s*=\s*'([^']+)';", page_res.text)
        if not match:
            if "wartung" in page_res.text.lower():
                raise UpstreamMaintenanceError(
                    f"Server {self.server} befindet sich im Wartungsmodus."
                )
            raise AuthenticationError("Konnte 'rid' nicht aus der Anmeldeseite extrahieren.")

        self.rid = match.group(1)
        self.circuit_breaker.record_success()
        logger.info(f"Erfolgreich angemeldet an Server {self.server}. RID: {self.rid}")
        return True

    async def api_call(
        self,
        endpoint: str,
        params: dict[str, Any],
        auto_relogin: bool = True,
        max_retries: int = 3,
    ) -> dict[str, Any]:
        """Execute an authenticated AJAX call with human-like jitter and session recovery."""
        self.circuit_breaker.check_state()
        if not self.rid:
            if auto_relogin and self.username and self.password:
                logger.info("Keine aktive Session. Führe initialen Login durch...")
                await self.login()
            else:
                raise SessionExpiredError("api_call kann ohne aktive RID nicht ausgeführt werden.")

        return await self._execute_with_retry(
            endpoint=endpoint,
            params=params,
            auto_relogin=auto_relogin,
            retries_left=max_retries,
        )

    async def _execute_with_retry(
        self,
        endpoint: str,
        params: dict[str, Any],
        auto_relogin: bool,
        retries_left: int,
    ) -> dict[str, Any]:
        """Internal worker executing the request, catching session expiration and retrying."""
        self.circuit_breaker.check_state()
        # Anti-detection jitter
        jitter_s = random.uniform(self.min_jitter_ms / 1000.0, self.max_jitter_ms / 1000.0)
        await asyncio.sleep(jitter_s)

        url = f"https://s{self.server}.myfreefarm.de/ajax/{endpoint}.php"
        query_params = {"rid": self.rid, **params}
        logger.debug(f"API Call -> {endpoint}.php mit Parametern: {mask_sensitive_data(params)}")

        try:
            res = await self.client.get(url, params=query_params)
        except httpx.TimeoutException as e:
            self.circuit_breaker.record_failure(f"Timeout bei Aufruf von {endpoint}.php")
            raise NetworkTimeoutError(f"Timeout bei Aufruf von {endpoint}.php: {e}") from e

        if res.status_code == 429:
            self.circuit_breaker.trip(f"HTTP 429 Too Many Requests von {endpoint}.php")
            raise UpstreamAPIError(f"HTTP 429 Too Many Requests für {endpoint}")

        if res.status_code == 503 or "wartung" in res.text.lower():
            raise UpstreamMaintenanceError(
                f"Server {self.server} ist im Wartungsmodus (HTTP {res.status_code})"
            )

        if res.status_code != 200:
            self.circuit_breaker.record_failure(f"HTTP {res.status_code} von {endpoint}.php")
            raise UpstreamAPIError(f"HTTP {res.status_code} für {endpoint}: {res.text[:200]}")

        # Check if response is valid JSON or redirected HTML
        try:
            data = res.json()
        except json.JSONDecodeError:
            raw_text = res.text.strip().lower()
            if "login" in raw_text or "session" in raw_text:
                data = {"status": "error", "message": "session expired (html response)"}
            elif raw_text == "failed":
                self.circuit_breaker.trip(
                    f"Upstream Server meldete 'failed' auf {endpoint}.php (Rate-Limit/Session-Block)"
                )
                raise UpstreamAPIError(
                    f"Kein valides JSON von {endpoint}.php erhalten: failed"
                )
            else:
                self.circuit_breaker.record_failure(f"Kein valides JSON von {endpoint}.php")
                raise UpstreamAPIError(
                    f"Kein valides JSON von {endpoint}.php erhalten: {res.text[:200]}"
                )

        # Check for session expiration indicators
        is_expired = False
        if isinstance(data, dict):
            msg = str(data.get("message", "")).lower()
            if data.get("status") == "error" and (
                "login" in msg or "session" in msg or "rid" in msg
            ):
                is_expired = True
            elif (
                isinstance(data.get("datablock"), (list, tuple))
                and len(data["datablock"]) > 0
                and data["datablock"][0] == 0
            ):
                block_err = str(data["datablock"]).lower()
                if "login" in block_err or "session" in block_err:
                    is_expired = True

        if is_expired:
            if auto_relogin and retries_left > 0 and self.username and self.password:
                wait_s = 2 ** (3 - retries_left)  # 1s, 2s, 4s backoff
                logger.warning(
                    f"Session/RID abgelaufen. Auto-Re-Login Versuch ({4 - retries_left}/3) in {wait_s}s..."
                )
                await asyncio.sleep(wait_s)
                await self.login()
                return await self._execute_with_retry(
                    endpoint=endpoint,
                    params=params,
                    auto_relogin=auto_relogin,
                    retries_left=retries_left - 1,
                )
            raise SessionExpiredError(f"Sitzung abgelaufen oder RID ungültig: {data}")

        if isinstance(data, dict) and data.get("status") == "error":
            raise UpstreamAPIError(f"Upstream Fehler von {endpoint}.php: {data}")

        if isinstance(data, dict) and "melde dich bitte beim support" in str(data).lower():
            self.circuit_breaker.trip(f"Upjers Sperre auf {endpoint}.php erkannt")
            raise UpstreamAPIError(f"Upjers Sperre auf {endpoint}.php erkannt: {data}")

        self.circuit_breaker.record_success()
        return data

    async def logout(self):
        """Cleanly terminate session on game server."""
        if self._client:
            url = f"https://s{self.server}.myfreefarm.de/main.php?page=logout&logoutbutton=1"
            try:
                await self.client.get(url)
            except httpx.HTTPError as e:
                logger.warning(f"Fehler beim Logout: {e}")
            await self.close()
            logger.info(f"Erfolgreich von Server {self.server} abgemeldet.")

    async def close(self):
        """Close underlying HTTP connection pool."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        self._client = None
        self.rid = None


# ---------------------------------------------------------------------------
# CLI Entrypoint for Smoke-Testing Live Credentials
# ---------------------------------------------------------------------------
async def _cli_main():
    from app.config import settings

    setup_logging(debug=True)
    parser = argparse.ArgumentParser(description="MyFreeFarm Live Client Smoke-Test")
    parser.add_argument("--server", type=int, default=settings.account.server, help="Server Nummer")
    parser.add_argument(
        "--username", type=str, default=settings.account.username, help="Account Benutzername"
    )
    parser.add_argument(
        "--password", type=str, default=settings.account.password, help="Account Passwort"
    )
    args = parser.parse_args()

    if not args.username or not args.password:
        logger.error(
            "Keine Zugangsdaten angegeben. Bitte in .env eintragen oder per CLI übergeben."
        )
        sys.exit(1)

    logger.info(f"=== Starte Live Smoke-Test für Server {args.server}, User '{args.username}' ===")
    client = MFFGameClient(server=args.server, username=args.username, password=args.password)
    try:
        await client.login()
        logger.info("Login erfolgreich! Teste Abruf von 'getfarms'...")
        res = await client.api_call("farm", {"mode": "getfarms", "farm": 1, "position": 0})
        logger.info(
            f"getfarms erfolgreich empfangen: datablock Status = {res.get('datablock', [None])[0]}"
        )
    except (MFFException, httpx.HTTPError) as e:
        logger.error(f"Smoke-Test fehlgeschlagen: {e}")
        sys.exit(1)
    finally:
        await client.logout()
    logger.info("=== Smoke-Test erfolgreich abgeschlossen ===")


if __name__ == "__main__":
    asyncio.run(_cli_main())
