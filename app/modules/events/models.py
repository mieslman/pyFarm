from typing import Any

from pydantic import BaseModel, Field


class ActiveEvent(BaseModel):
    """Represents an active seasonal event detected from the game config."""

    name: str
    raw_data: dict[str, Any] = Field(default_factory=dict)


class CalendarStatus(BaseModel):
    """Status of the advent / event calendar."""

    day: int = 0
    unopened_days: list[int] = Field(default_factory=list)
    is_opened_today: bool = False


class DeliverySpot(BaseModel):
    """Target spot for a delivery event tour."""

    spot_id: str
    name: str = ""
    points: int = 0
    duration: int = 0
    outcome: float = 0.0


class DeliveryStatus(BaseModel):
    """Status of the delivery tours event."""

    points: int = 0
    active_tour_remain: int = 0
    active_spot: str = ""
    selected_spot: str | None = None


class EventGardenTile(BaseModel):
    """A tile in the event garden."""

    tile_id: str
    remain: int = 0
    status: int = 0
    plant: str | int = 0


class EventGardenStatus(BaseModel):
    """Status of the event garden."""

    tiles_count: int = 0
    ripe_count: int = 0
    available_seeds: dict[str, int] = Field(default_factory=dict)


class OktoberfestSheep(BaseModel):
    """A sheep waiting to be seated at the beer table."""

    sheep_id: str
    interests: list[str] = Field(default_factory=list)
    seated: bool = False


class OktoberfestStatus(BaseModel):
    """Status of the Oktoberfest sheep seating minigame."""

    cooldown_remain: int = 0
    sheeps_count: int = 0
    is_ready: bool = False


class PentecostStatus(BaseModel):
    """Status of the Pentecost plant care event."""

    water_remain: int = 0
    fertilizer_remain: int = 0
    available_water: int = 0
    available_fertilizer: int = 0
    required_water: int = 0
    required_fertilizer: int = 0


class OlympiaStatus(BaseModel):
    """Status of the Olympia event."""

    energy: int = 0
    berries: int = 0
    can_participate: bool = False


class EventsOverview(BaseModel):
    """Consolidated status overview for all seasonal events."""

    active_events: list[str] = Field(default_factory=list)
    calendar: CalendarStatus | None = None
    delivery: DeliveryStatus | None = None
    eventgarden: EventGardenStatus | None = None
    oktoberfest: OktoberfestStatus | None = None
    pentecost: PentecostStatus | None = None
    olympia: OlympiaStatus | None = None
    last_run: str | None = None
