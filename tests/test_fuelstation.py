import httpx
import pytest
import respx

from app.config import FuelstationConfig
from app.core.client import MFFGameClient
from app.models.product import Product
from app.modules.farm_buildings.fuelstation import Fuelstation
from app.services.stock_service import StockService


@pytest.mark.asyncio
async def test_fuelstation_status_parsing(fast_client: MFFGameClient):
    """Test parsing of Fuelstation slots, levels, points, and production timers."""
    fs = Fuelstation(fast_client, farm_id=4, position=6)

    building_data = {
        "level": 9,
        "data": {
            "data": {
                "count": "146521",
                "slots": {
                    "1": {
                        "level": 5,
                        "points_left": 6821,
                        "block": 0,
                        "products": {"17": {"points": 160}, "2": {"points": 1128}},
                    },
                    "2": {
                        "level": 3,
                        "points_left": 2964,
                        "block": 0,
                        "products": {"17": {"points": 160}},
                    },
                },
            }
        },
        "production": [
            {"slot": "1", "pid": "350", "remain": -500},  # Finished!
            {"slot": "2", "pid": "350", "remain": 15000},  # Busy
        ],
    }

    state = fs.update(building_data)
    assert state.level == 9
    assert state.tokens == 146521
    assert len(state.slots) == 2

    slot1 = state.slots[1]
    assert slot1.level == 5
    assert slot1.busy is True
    assert slot1.remain == -500
    assert slot1.is_finished is True

    slot2 = state.slots[2]
    assert slot2.level == 3
    assert slot2.busy is True
    assert slot2.remain == 15000
    assert slot2.is_finished is False


@pytest.mark.asyncio
async def test_fuelstation_harvest(fast_client: MFFGameClient):
    """Test harvesting finished canister from Fuelstation slot."""
    fast_client.rid = "test_rid"
    fs = Fuelstation(fast_client, farm_id=4, position=6)

    building_data = {
        "level": 9,
        "data": {
            "data": {"count": 100, "slots": {"1": {"level": 1, "points_left": 0, "products": {}}}}
        },
        "production": [{"slot": "1", "remain": 0}],
    }
    fs.update(building_data)
    assert fs.slots[1].is_finished is True

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": [1]})
        )

        harvested = await fs.harvest()
        assert harvested == 1
        assert fs.slots[1].busy is False
        assert fs.slots[1].remain == 0


@pytest.mark.asyncio
async def test_fuelstation_refill_optimal_crop(fast_client: MFFGameClient):
    """Test refilling empty slot with preferred surplus crop."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    carrot = Product(pid=17, name="Karotte", price=0.34, category="v", amount=800)
    stock.init_with_catalog({17: carrot})

    config = FuelstationConfig(
        enabled=True,
        auto_refill=True,
        min_reserve=500,  # Surplus is 800 - 500 = 300
        preferred_pids=[17],
    )
    fs = Fuelstation(fast_client, farm_id=4, position=6, config=config)

    # Slot 1 requires 1600 points (10 carrots at 160 pts each)
    building_data = {
        "level": 9,
        "data": {
            "data": {
                "count": 100,
                "slots": {
                    "1": {
                        "level": 1,
                        "points_left": 1600,
                        "block": 0,
                        "products": {"17": {"points": 160}},
                    }
                },
            }
        },
        "production": [],
    }
    fs.update(building_data)
    assert fs.slots[1].is_waiting_for_refill is True

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": [1]})
        )

        refilled = await fs.refill(stock)
        assert refilled == 1
        assert fs.slots[1].points_left == 0
        assert fs.slots[1].busy is True


@pytest.mark.asyncio
async def test_fuelstation_refill_respects_reserve(fast_client: MFFGameClient):
    """Test refilling is skipped if stock is below min_reserve."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    # Only 400 carrots in stock, below reserve 500
    carrot = Product(pid=17, name="Karotte", price=0.34, category="v", amount=400)
    stock.init_with_catalog({17: carrot})

    config = FuelstationConfig(min_reserve=500, preferred_pids=[17])
    fs = Fuelstation(fast_client, farm_id=4, position=6, config=config)

    building_data = {
        "data": {
            "data": {
                "slots": {
                    "1": {
                        "points_left": 1600,
                        "block": 0,
                        "products": {"17": {"points": 160}},
                    }
                }
            }
        },
        "production": [],
    }
    fs.update(building_data)

    refilled = await fs.refill(stock)
    assert refilled == 0


@pytest.mark.asyncio
async def test_fuelstation_refill_production_limit_and_entries(fast_client: MFFGameClient):
    """Test Level 5 slot with 5.000.000 limit, existing entries, and multi-crop fill."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    # Stock has 100 Karotten and 10.000 Mais
    carrot = Product(pid=17, name="Karotten", price=0.34, category="v", amount=600)  # surplus: 100
    corn = Product(pid=2, name="Mais", price=0.85, category="v", amount=10000)  # surplus: 9500
    stock.init_with_catalog({17: carrot, 2: corn})

    config = FuelstationConfig(min_reserve=500, preferred_pids=[17, 2])
    fs = Fuelstation(fast_client, farm_id=4, position=6, config=config)

    building_data = {
        "data": {
            "constants": {
                "slot_level": {
                    "5": {"limit": 5000000, "output": 5, "duration": 32400},
                }
            },
            "data": {
                "count": 7349,
                "slots": {
                    "1": {
                        "level": 5,
                        "points_left": 6821,  # level upgrade points left
                        "block": 0,
                        "products": {"17": {"points": 160}, "2": {"points": 1128}},
                        "entries": {"17": 43},  # 43 * 160 = 6880 current points
                    }
                },
            },
        },
        "production": [],
    }
    fs.update(building_data)

    slot1 = fs.slots[1]
    assert slot1.level == 5
    assert slot1.production_limit == 5000000
    assert slot1.current_points == 6880
    assert slot1.points_needed == 4993120
    assert slot1.is_waiting_for_refill is True

    # When refilling:
    # 1. First tries PID 17: has 100 surplus -> inserts 100x Karotten (16.000 pts)
    #    current_points becomes 6880 + 16000 = 22880 pts, needed: 4977120 pts
    # 2. Continues to PID 2: needs ceil(4977120 / 1128) = 4413 Mais
    #    corn has 9500 surplus -> inserts 4413x Mais (4.977.864 pts)
    #    slot is full!
    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": [1]})
        )

        refilled = await fs.refill(stock)
        assert refilled == 1
        assert slot1.current_points >= slot1.production_limit
        assert slot1.points_needed == 0
        assert slot1.busy is True

