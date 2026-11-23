"""In-memory schedule cache for skipping still-running fields, sheds, and factories."""

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class ScheduledBuilding:
    """Cached next-check deadline and state snapshot for a running building."""

    next_check_at: float
    recorded_at: float
    remain_seconds: int
    state: Any = None


class ProductionScheduleCache:
    """Tracks when running fields, sheds, and factories next need an upstream API check.

    Avoids redundant ``gardeninit``, ``inner_init``, and ``innerinfos`` calls for buildings
    whose production is known to be running with remaining time > 0. Caps skips at
    ``max_skip_seconds`` (default 1 hour) and subtracts ``safety_margin_seconds`` (default 15s)
    so harvests, watering renewals, and manual player changes are never missed for long.
    """

    DEFAULT_SAFETY_MARGIN_SECONDS: int = 15
    DEFAULT_MAX_SKIP_SECONDS: int = 3600

    def __init__(
        self,
        safety_margin_seconds: int = DEFAULT_SAFETY_MARGIN_SECONDS,
        max_skip_seconds: int = DEFAULT_MAX_SKIP_SECONDS,
    ) -> None:
        self.safety_margin_seconds = max(0, safety_margin_seconds)
        self.max_skip_seconds = max(0, max_skip_seconds)
        self._entries: dict[tuple[str, int, int], ScheduledBuilding] = {}

    def should_skip(self, kind: str, farm_id: int, position: int) -> ScheduledBuilding | None:
        """Return cached entry if building is still within its skip window, else None."""
        key = (kind, farm_id, position)
        entry = self._entries.get(key)
        if entry is None:
            return None

        if time.monotonic() < entry.next_check_at:
            return entry

        self._entries.pop(key, None)
        return None

    def record(
        self,
        kind: str,
        farm_id: int,
        position: int,
        remain_seconds: int,
        state: Any = None,
    ) -> bool:
        """Schedule the next check for a running building. Returns True if cached."""
        key = (kind, farm_id, position)
        effective_wait = min(
            max(0, int(remain_seconds) - self.safety_margin_seconds),
            self.max_skip_seconds,
        )
        if effective_wait <= 0:
            self._entries.pop(key, None)
            return False

        now = time.monotonic()
        self._entries[key] = ScheduledBuilding(
            next_check_at=now + effective_wait,
            recorded_at=now,
            remain_seconds=int(remain_seconds),
            state=state,
        )
        return True

    def invalidate(self, kind: str, farm_id: int, position: int) -> None:
        """Remove a single building from the schedule cache."""
        self._entries.pop((kind, farm_id, position), None)

    def clear(self) -> None:
        """Clear all cached schedules (e.g. after speed boost or powerup activation)."""
        self._entries.clear()

    @staticmethod
    def elapsed_seconds(entry: ScheduledBuilding) -> int:
        """Return whole seconds elapsed since the entry was recorded."""
        return max(0, int(time.monotonic() - entry.recorded_at))

    @staticmethod
    def estimated_remain_seconds(entry: ScheduledBuilding) -> int:
        """Estimate remaining production seconds for dashboard/log display."""
        elapsed = time.monotonic() - entry.recorded_at
        return max(0, int(entry.remain_seconds - elapsed))

    @staticmethod
    def remaining_skip_seconds(entry: ScheduledBuilding) -> int:
        """Return seconds remaining until the next upstream API check."""
        return max(0, int(entry.next_check_at - time.monotonic()))
