"""Global API Circuit Breaker preventing request storms on upstream rate-limits or bans."""

import time
from typing import Any

from loguru import logger

from app.core.exceptions import CircuitBreakerOpenError


class CircuitBreaker:
    """Monitors upstream API health and halts requests on rate-limiting, bans, or consecutive failures."""

    def __init__(
        self,
        failure_threshold: int = 2,
        recovery_timeout_s: float = 900.0,  # 15 minutes
    ) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout_s = recovery_timeout_s
        self.state: str = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.failure_count: int = 0
        self.last_tripped_at: float | None = None
        self.trip_reason: str | None = None
        self.total_trips: int = 0

    @property
    def is_open(self) -> bool:
        """Check if circuit is currently open without raising exception."""
        if self.state == "OPEN":
            if self.last_tripped_at and (time.time() - self.last_tripped_at > self.recovery_timeout_s):
                self.state = "HALF_OPEN"
                logger.info("CircuitBreaker: Cooldown abgelaufen. Übergang in Zustand 'HALF_OPEN'.")
                return False
            return True
        return False

    def check_state(self) -> None:
        """Verify circuit breaker state before initiating an upstream request.

        Raises:
            CircuitBreakerOpenError: If the circuit is currently OPEN and within recovery cooldown.
        """
        if self.state == "OPEN":
            if self.last_tripped_at and (time.time() - self.last_tripped_at > self.recovery_timeout_s):
                self.state = "HALF_OPEN"
                logger.info(
                    "CircuitBreaker: Cooldown abgelaufen. Erlaube einzelnen Testaufruf (HALF_OPEN)."
                )
                return

            remaining = 0
            if self.last_tripped_at:
                remaining = max(0, int(self.recovery_timeout_s - (time.time() - self.last_tripped_at)))

            msg = (
                f"Circuit-Breaker ist AKTIV ({self.trip_reason or 'Upstream-Blockade'}). "
                f"Alle Anfragen sind für weitere {remaining}s gesperrt."
            )
            raise CircuitBreakerOpenError(msg)

    def record_success(self) -> None:
        """Record a successful API response and heal circuit if in HALF_OPEN."""
        if self.state == "HALF_OPEN":
            logger.info("CircuitBreaker: Testaufruf erfolgreich. Schließe Circuit-Breaker (CLOSED).")
            self.reset()
        else:
            self.failure_count = 0

    def record_failure(self, reason: str, critical: bool = False) -> None:
        """Record an API failure. If critical or threshold reached, trip the circuit.

        Args:
            reason: Description of the failure (e.g. 'HTTP 429', 'failed', 'Bot detected').
            critical: If True, trip immediately without waiting for threshold.
        """
        self.failure_count += 1
        logger.warning(
            f"CircuitBreaker: Fehler registriert ({self.failure_count}/{self.failure_threshold}): {reason}"
        )

        if critical or self.failure_count >= self.failure_threshold or self.state == "HALF_OPEN":
            self.trip(reason)

    def trip(self, reason: str) -> None:
        """Immediately trip the circuit breaker into OPEN state."""
        self.state = "OPEN"
        self.last_tripped_at = time.time()
        self.trip_reason = reason
        self.total_trips += 1
        logger.error(
            f"🚨 Circuit-Breaker AKTIVIERT: {reason}. "
            f"Alle weiteren API-Aufrufe werden für {int(self.recovery_timeout_s)}s blockiert!"
        )

    def reset(self) -> None:
        """Manually or automatically reset the circuit breaker to normal CLOSED state."""
        self.state = "CLOSED"
        self.failure_count = 0
        self.last_tripped_at = None
        self.trip_reason = None
        logger.info("CircuitBreaker: Auf Normalzustand 'CLOSED' zurückgesetzt.")

    def get_status(self) -> dict[str, Any]:
        """Return structured status for API and Web Dashboard."""
        remaining_s = 0
        if self.state == "OPEN" and self.last_tripped_at:
            remaining_s = max(0, int(self.recovery_timeout_s - (time.time() - self.last_tripped_at)))

        return {
            "state": self.state,
            "is_open": self.is_open,
            "failure_count": self.failure_count,
            "threshold": self.failure_threshold,
            "trip_reason": self.trip_reason,
            "last_tripped_at": self.last_tripped_at,
            "cooldown_remaining_seconds": remaining_s,
            "total_trips": self.total_trips,
        }


# Global singleton instance
circuit_breaker = CircuitBreaker()
