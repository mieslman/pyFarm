"""Tests for ProductionScheduleCache and skipping running fields, sheds, and factories."""

import pytest

from app.config import FarmStrategyConfig
from app.core.client import MFFGameClient
from app.modules.farm_buildings.farm_service import FarmService
from app.modules.farm_buildings.schedule_cache import ProductionScheduleCache
from app.services.stock_service import StockService


def test_schedule_cache_basic_and_bounds():
    """Verify safety margin, max_skip cap, invalidation, and clear."""
    cache = ProductionScheduleCache(safety_margin_seconds=15, max_skip_seconds=3600)

    # Too close to completion -> not cached
    assert cache.record("shed", 2, 2, remain_seconds=10) is False
    assert cache.should_skip("shed", 2, 2) is None

    # Normal running shed -> cached with safety margin subtracted
    assert cache.record("shed", 2, 2, remain_seconds=600, state="barn_state") is True
    entry = cache.should_skip("shed", 2, 2)
    assert entry is not None
    assert entry.state == "barn_state"
    assert 580 <= cache.remaining_skip_seconds(entry) <= 585

    # Very long production -> capped at max_skip_seconds (3600)
    assert cache.record("shed", 2, 6, remain_seconds=59868) is True
    long_entry = cache.should_skip("shed", 2, 6)
    assert long_entry is not None
    assert 3595 <= cache.remaining_skip_seconds(long_entry) <= 3600

    # Single invalidation
    cache.invalidate("shed", 2, 2)
    assert cache.should_skip("shed", 2, 2) is None
    assert cache.should_skip("shed", 2, 6) is not None

    # Full clear
    cache.clear()
    assert cache.should_skip("shed", 2, 6) is None


@pytest.mark.asyncio
async def test_farm_service_skips_running_field_shed_and_factory(fast_client: MFFGameClient):
    """Running field, shed, and factory should be skipped on the second cycle and retain cached state."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    cache = ProductionScheduleCache(safety_margin_seconds=15, max_skip_seconds=3600)

    # Build 120 watered, growing tiles for field 1/1
    full_tiles = {
        str(i): {"inhalt": "17", "phase": "2", "remain": "3000", "iswater": "1"}
        for i in range(1, 121)
    }

    getfarms_payload = {
        "updateblock": {
            "farms": {
                "config": {"building2product": {"2": "9"}, "advancedbuilding": {"8": {}}},
                "farms": {
                    "1": {
                        "1": {"buildingid": "1", "name": "Acker 1"},
                        "2": {"buildingid": "2", "name": "Hühnerstall"},
                        "3": {"buildingid": "8", "name": "Käserei"},
                    }
                },
            }
        }
    }

    call_counts: dict[str, int] = {}

    async def fake_api_call(endpoint: str, params: dict) -> dict:
        mode = str(params.get("mode", ""))
        call_counts[mode] = call_counts.get(mode, 0) + 1
        if mode == "getfarms":
            return getfarms_payload
        if mode == "gardeninit":
            return {"datablock": [1, full_tiles]}
        if mode == "inner_init":
            return {
                "datablock": [
                    1,
                    {
                        "1": {
                            "2": {
                                "pid": "9",
                                "animals": {"amount": 10},
                                "remain": 5000,
                                "rest": 7200,
                                "feed": {"1": {"time": 600}},
                            }
                        }
                    },
                ]
            }
        if mode == "innerinfos":
            return {
                "datablock": [
                    1,
                    {
                        "level": 2,
                        "slots": {
                            "1": {"block": 0, "ready": 0, "pid": 11, "amount": 15, "remain": 4000},
                            "2": {"block": 1},
                        },
                        "products": {},
                    },
                ]
            }
        return {"datablock": [1]}

    fast_client.api_call = fake_api_call  # type: ignore[method-assign]
    cfg = FarmStrategyConfig(auto_crop=True, auto_water=True)

    # Cycle 1: cold cache -> all 3 buildings are queried
    fs1 = FarmService(fast_client, config=cfg, schedule_cache=cache)
    await fs1.loop(stock_service=stock)
    assert call_counts.get("gardeninit") == 1
    assert call_counts.get("inner_init") == 1
    assert call_counts.get("innerinfos") == 1

    # Cycle 2: new FarmService sharing the same cache -> all 3 running buildings skipped!
    fs2 = FarmService(fast_client, config=cfg, schedule_cache=cache)
    await fs2.loop(stock_service=stock)
    assert call_counts.get("gardeninit") == 1
    assert call_counts.get("inner_init") == 1
    assert call_counts.get("innerinfos") == 1

    # Restored state must still be populated for dashboard/API inspection
    assert len(fs2.fields[0].tiles) == 120
    assert fs2.sheds[0].barn is not None
    assert fs2.sheds[0].barn.animals_count == 10
    assert fs2.sheds[0].barn.remain_seconds > 0
    assert 1 in fs2.factories[0].slots
    assert fs2.factories[0].slots[1].is_active is True


@pytest.mark.asyncio
async def test_farm_service_does_not_skip_unwatered_field_or_idle_factory(
    fast_client: MFFGameClient,
):
    """Fields that still need watering or factories with free unlocked slots must NOT be skipped."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    cache = ProductionScheduleCache(safety_margin_seconds=15, max_skip_seconds=3600)

    # Field is only partially planted (60 tiles) -> must NOT be skipped
    partial_tiles = {
        str(i): {"inhalt": "17", "phase": "2", "remain": "3000", "iswater": "1"}
        for i in range(1, 61)
    }

    getfarms_payload = {
        "updateblock": {
            "farms": {
                "config": {"building2product": {}, "advancedbuilding": {"8": {}}},
                "farms": {
                    "1": {
                        "1": {"buildingid": "1", "name": "Teil-Acker"},
                        "3": {"buildingid": "8", "name": "Freie Käserei"},
                    }
                },
            }
        }
    }

    call_counts: dict[str, int] = {}

    async def fake_api_call(endpoint: str, params: dict) -> dict:
        mode = str(params.get("mode", ""))
        call_counts[mode] = call_counts.get(mode, 0) + 1
        if mode == "getfarms":
            return getfarms_payload
        if mode == "gardeninit":
            return {"datablock": [1, partial_tiles]}
        if mode == "innerinfos":
            return {
                "datablock": [
                    1,
                    {
                        "level": 1,
                        "slots": {
                            "1": {"block": 0, "ready": 0, "pid": 11, "amount": 15, "remain": 4000},
                            "2": [],  # Unlocked empty slot!
                        },
                        "products": {},
                    },
                ]
            }
        return {"datablock": [1]}

    fast_client.api_call = fake_api_call  # type: ignore[method-assign]
    cfg = FarmStrategyConfig(auto_crop=True, auto_water=True)

    fs1 = FarmService(fast_client, config=cfg, schedule_cache=cache)
    await fs1.loop(stock_service=stock)
    assert call_counts.get("gardeninit") == 1
    assert call_counts.get("innerinfos") == 1

    # Cycle 2: neither should be skipped
    fs2 = FarmService(fast_client, config=cfg, schedule_cache=cache)
    await fs2.loop(stock_service=stock)
    assert call_counts.get("gardeninit") == 2
    assert call_counts.get("innerinfos") == 2
