from typing import Any
from pydantic import BaseModel, Field


class SeasonPassTask(BaseModel):
    """Represents a single season pass task entry returned by the upstream API."""

    id: str
    type: str
    payload: dict[str, Any] = Field(default_factory=dict, description="Inner data payload (count, done, pid, etc.)")
    created_at: int | None = None
    finish_at: int | None = None
    points: int | None = None
    remain: int | None = None
    unr: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "SeasonPassTask":
        payload = data.get("data", {}) if isinstance(data.get("data"), dict) else {}
        created_at = int(data["createdate"]) if "createdate" in data and str(data["createdate"]).isdigit() else None
        finish_at = int(data["finishdate"]) if "finishdate" in data and str(data["finishdate"]).isdigit() else None
        points = int(payload["points"]) if "points" in payload and str(payload["points"]).isdigit() else None
        remain = int(data["remain"]) if "remain" in data and str(data["remain"]).isdigit() else None
        unr = str(data.get("unr", "")) or None

        return cls(
            id=str(data.get("id", "")),
            type=str(data.get("type", "")),
            payload=payload,
            created_at=created_at,
            finish_at=finish_at,
            points=points,
            remain=remain,
            unr=unr,
        )

    @property
    def is_completed(self) -> bool:
        """Return True if the task has been finished by the player."""
        return self.finish_at is not None and self.finish_at > 0

    @property
    def pid(self) -> int | None:
        """Product ID if specified in task payload."""
        p = self.payload.get("pid")
        if p is not None and str(p).isdigit():
            return int(p)
        return None

    @property
    def building(self) -> int | None:
        """Building ID if specified in task payload."""
        b = self.payload.get("building")
        if b is not None and str(b).isdigit():
            return int(b)
        return None

    @property
    def count(self) -> int:
        """Target count for the task."""
        c = self.payload.get("count", 1)
        try:
            return int(c)
        except (ValueError, TypeError):
            return 1

    @property
    def done(self) -> int:
        """Already completed progress count."""
        d = self.payload.get("done", 0)
        try:
            return int(d)
        except (ValueError, TypeError):
            return 0

    @property
    def progress_ratio(self) -> float:
        """Completion ratio between 0.0 and 1.0."""
        if self.count <= 0:
            return 1.0 if self.is_completed else 0.0
        return min(1.0, max(0.0, self.done / self.count))


class SeasonPassLevel(BaseModel):
    """Represents a milestone tier level in the season pass."""

    level: int
    points: int = 0
    free_rewards: dict[str, Any] = Field(default_factory=dict)
    premium_rewards: dict[str, Any] = Field(default_factory=dict)
    is_claimed: bool = False


class SeasonPassSnapshot(BaseModel):
    """A complete snapshot of the season pass state."""

    season_id: str = ""
    season_name: str = ""
    points: int = 0
    remain: int = 0
    pending_tasks: list[SeasonPassTask] = Field(default_factory=list)
    completed_tasks: list[SeasonPassTask] = Field(default_factory=list)
    rewards_claimed: dict[str, Any] = Field(default_factory=dict)
    levels: dict[int, SeasonPassLevel] = Field(default_factory=dict)

    @classmethod
    def from_api(cls, body: dict[str, Any]) -> "SeasonPassSnapshot":
        datablock = body.get("datablock", {}) if isinstance(body, dict) else {}
        data = datablock.get("data", {}) if isinstance(datablock, dict) else {}
        config = datablock.get("config", {}) if isinstance(datablock, dict) else {}

        season_id = str(data.get("id", ""))
        season_name = str(data.get("name", ""))
        points = int(data.get("points", 0)) if str(data.get("points", 0)).isdigit() else 0
        remain = int(data.get("remain", 0)) if str(data.get("remain", 0)).isdigit() else 0

        pending_tasks = [
            SeasonPassTask.from_api(t) for t in data.get("tasks", []) if isinstance(t, dict)
        ]
        completed_tasks = [
            SeasonPassTask.from_api(t) for t in data.get("tasksDone", []) if isinstance(t, dict)
        ]
        rewards_claimed = data.get("rewards", {}) if isinstance(data.get("rewards"), dict) else {}

        # Parse level tiers from config.constants.levels if present
        levels: dict[int, SeasonPassLevel] = {}
        constants = config.get("constants", {}) if isinstance(config, dict) else {}
        raw_levels = constants.get("levels", {}) if isinstance(constants, dict) else {}
        if isinstance(raw_levels, dict):
            for lvl_str, lvl_data in raw_levels.items():
                if str(lvl_str).isdigit() and isinstance(lvl_data, dict):
                    lvl_num = int(lvl_str)
                    pts = int(lvl_data.get("points", 0))
                    free_rw = lvl_data.get("free", {}) if isinstance(lvl_data.get("free"), dict) else {}
                    prem_rw = lvl_data.get("premium", {}) if isinstance(lvl_data.get("premium"), dict) else {}
                    claimed = lvl_str in rewards_claimed and "free" in rewards_claimed[lvl_str]
                    levels[lvl_num] = SeasonPassLevel(
                        level=lvl_num,
                        points=pts,
                        free_rewards=free_rw,
                        premium_rewards=prem_rw,
                        is_claimed=claimed,
                    )

        return cls(
            season_id=season_id,
            season_name=season_name,
            points=points,
            remain=remain,
            pending_tasks=pending_tasks,
            completed_tasks=completed_tasks,
            rewards_claimed=rewards_claimed,
            levels=levels,
        )


class SeasonPassConfig(BaseModel):
    """Configuration settings for automated season pass handling."""

    enabled: bool = Field(default=True, description="Seasonpass-Automatisierung aktiv")
    auto_claim_rewards: bool = Field(default=True, description="Freie Stufenbelohnungen automatisch abholen")
    preferred_field_farm: int = Field(default=1, description="Farm-ID für Ackerbau-Tasks")
    preferred_field_pos: int = Field(default=1, description="Feldposition für Ackerbau-Tasks")
    preferred_forestry_pos: int = Field(default=1, description="Baumplatz-Position für Forst-Tasks")
