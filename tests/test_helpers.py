import httpx
import pytest
import respx

from app.core.client import MFFGameClient
from app.models.product import Product
from app.modules.helpers.helpers_service import HelpersService
from app.services.stock_service import StockService


@pytest.mark.asyncio
async def test_farm_dog_bonus(fast_client: MFFGameClient):
    """Test Braver Ben daily bonus claim when available."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    helpers = HelpersService(fast_client, stock_service=stock)

    # 1. Available (farmdog_harvest == 0)
    menue_available = {"farmdog_harvest": 0}
    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": 1})
        )
        claimed = await helpers.handle_farm_dog(menue_available)
        assert claimed is True

    # 2. Already claimed today
    menue_claimed = {"farmdog_harvest": 1}
    claimed_again = await helpers.handle_farm_dog(menue_claimed)
    assert claimed_again is False


@pytest.mark.asyncio
async def test_donkey_bonus(fast_client: MFFGameClient):
    """Test Goldesel Waltraud bonus claim."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    helpers = HelpersService(fast_client, stock_service=stock)

    # 1. Ready (donkey == 1)
    menue_ready = {"donkey": 1}
    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"datablock": [1]})
        )
        milked = await helpers.handle_donkey(menue_ready)
        assert milked is True

    # 2. Not ready (donkey == 0)
    menue_not_ready = {"donkey": 0}
    assert await helpers.handle_donkey(menue_not_ready) is False


@pytest.mark.asyncio
async def test_lottery_daily_prize(fast_client: MFFGameClient):
    """Test drawing daily free lottery ticket at City 2 lottery booth."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    helpers = HelpersService(fast_client, stock_service=stock)

    # datablock[2] == 0 means free ticket is waiting
    init_data = {"datablock": [1, {}, 0]}
    newlot_data = {"datablock": [1]}
    prize_data = {"datablock": [1]}

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/city.php").mock(
            side_effect=[
                httpx.Response(200, json=init_data),
                httpx.Response(200, json=newlot_data),
                httpx.Response(200, json=prize_data),
            ]
        )

        won = await helpers.handle_lottery()
        assert won is True


@pytest.mark.asyncio
async def test_windmill_crop_and_produce(fast_client: MFFGameClient):
    """Test Windmill harvesting ready product and starting new production with available ingredients."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    # Seed wheat in stock
    wheat = Product(pid=1, name="Weizen", price=0.10, category="v", amount=50)
    stock.init_with_catalog({1: wheat})

    helpers = HelpersService(fast_client, stock_service=stock)

    init_ready_to_crop = {
        "datablock": [
            1,
            {"1": [101, 1, "Weißbrot", [[1, 10]], 1]},  # 10x wheat required
            {"1": {"slot": 1, "remain": 0}},
            {},
            {"ready": 1, "running": 0},
        ]
    }

    init_idle = {
        "datablock": [
            1,
            {"1": [101, 1, "Weißbrot", [[1, 10]], 1]},
            {"1": {"slot": 1, "remain": 0}},
            {},
            {"ready": 0, "running": 0},
        ]
    }

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/city.php").mock(
            side_effect=[
                httpx.Response(200, json=init_ready_to_crop),  # windmillinit (crop needed)
                httpx.Response(200, json={"datablock": [1]}),  # windmillcrop
                httpx.Response(200, json=init_idle),  # re-fetch windmillinit
                httpx.Response(200, json={"datablock": [1]}),  # windmillstartproduction
            ]
        )

        produced = await helpers.handle_windmill()
        assert produced is True


@pytest.mark.asyncio
async def test_windmill_php_object_formulas(fast_client: MFFGameClient):
    """Test Windmill handles recipes encoded as JSON objects from PHP (string-keyed dicts)."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    wheat = Product(pid=1, name="Weizen", price=0.10, category="v", amount=50)
    stock.init_with_catalog({1: wheat})

    helpers = HelpersService(fast_client, stock_service=stock)

    # Real upstream PHP response format where recipes are dicts with keys "0", "1", "2", "3", "amount"
    init_idle_php = {
        "datablock": [
            1,
            {
                "1": {
                    "0": 101,
                    "1": 1,
                    "2": "Weißbrot",
                    "3": [[1, 10]],
                    "amount": 1,
                }
            },
            {"1": {"slot": 1, "remain": 0}},
            {},
            {"ready": 0, "running": 0},
        ]
    }

    with respx.mock:
        respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/city.php",
            params__contains={"mode": "windmillinit"},
        ).mock(return_value=httpx.Response(200, json=init_idle_php))

        start_call = respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/city.php",
            params__contains={"mode": "windmillstartproduction"},
        ).mock(return_value=httpx.Response(200, json={"datablock": [1]}))

        produced = await helpers.handle_windmill()
        assert produced is True
        assert start_call.called
        # Check that formula id 101 was passed, NOT formula 0
        last_request = start_call.calls.last.request
        assert "formula=101" in str(last_request.url)


@pytest.mark.asyncio
async def test_loginbonus_claim(fast_client: MFFGameClient):
    """Test claiming unclaimed daily login bonus."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    helpers = HelpersService(fast_client, stock_service=stock)

    menue_data = {
        "loginbonus": {
            "status": 1,
            "config": {
                "rewards": {
                    "1": {"type": "money"},
                    "2": {"type": "products"},
                    "3": {"type": "plant"},
                }
            },
            "data": {
                "rewards": {
                    "1": {"money": 500, "done": 1789900000},
                    "2": {"products": {"1212": 1}},
                    "3": {"plants": {"17": 5}},
                }
            },
        }
    }

    claim_response = {
        "updateblock": {
            "menue": {
                "loginbonus": {
                    "data": {
                        "rewards": {
                            "1": {"money": 500, "done": 1789900000},
                            "2": {"products": {"1212": 1}, "done": 1789986400},
                        }
                    }
                }
            }
        }
    }

    with respx.mock:
        claim_call = respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/farm.php",
            params__contains={"mode": "loginbonus_getreward", "day": "2"},
        ).mock(return_value=httpx.Response(200, json=claim_response))

        claimed = await helpers.handle_loginbonus(menue_data)
        assert claimed == ["2"]
        assert claim_call.called


@pytest.mark.asyncio
async def test_loginbonus_already_claimed(fast_client: MFFGameClient):
    """Test login bonus when all eligible rewards are already claimed."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    helpers = HelpersService(fast_client, stock_service=stock)

    menue_data = {
        "loginbonus": {
            "status": 1,
            "data": {
                "rewards": {
                    "1": {"money": 500, "done": 1789900000},
                    "2": {"points": 1000, "done": 1789986400},
                }
            },
        }
    }

    claimed = await helpers.handle_loginbonus(menue_data)
    assert claimed == []


@pytest.mark.asyncio
async def test_greenhouse_bonus_ready(fast_client: MFFGameClient):
    """Test greenhouse bonus harvest when cooldown has passed (remain <= 0)."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    helpers = HelpersService(fast_client, stock_service=stock)

    init_response = {
        "status": 1,
        "datablock": {
            "status": 1,
            "data": {
                "remain": -50,
                "rooms": {"1": {"reward": 1}},
            },
        },
    }

    claim_response = {
        "status": 1,
        "datablock": {"status": 1, "reward": 1},
    }

    with respx.mock:
        init_call = respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/farm.php",
            params__contains={"mode": "greenhouse_init"},
        ).mock(return_value=httpx.Response(200, json=init_response))

        claim_call = respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/farm.php",
            params__contains={"mode": "greenhouse_get_bonus"},
        ).mock(return_value=httpx.Response(200, json=claim_response))

        harvested = await helpers.handle_greenhouse()
        assert harvested is True
        assert init_call.called
        assert claim_call.called


@pytest.mark.asyncio
async def test_greenhouse_bonus_on_cooldown(fast_client: MFFGameClient):
    """Test greenhouse bonus does nothing when cooldown is active (remain > 0)."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    helpers = HelpersService(fast_client, stock_service=stock)

    init_response = {
        "status": 1,
        "datablock": {
            "status": 1,
            "data": {
                "remain": 7200,
                "rooms": {"1": {"reward": 1}},
            },
        },
    }

    with respx.mock:
        init_call = respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/farm.php",
            params__contains={"mode": "greenhouse_init"},
        ).mock(return_value=httpx.Response(200, json=init_response))

        harvested = await helpers.handle_greenhouse()
        assert harvested is False
        assert init_call.called


@pytest.mark.asyncio
async def test_pet_parts_claim(fast_client: MFFGameClient):
    """Test claiming daily pet parts package when daily > 0."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    helpers = HelpersService(fast_client, stock_service=stock)

    fm_data = {"pets": {"daily": 1}}

    with respx.mock:
        buy_call = respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/farm.php",
            params__contains={"mode": "pets_buy_parts", "id": "1"},
        ).mock(return_value=httpx.Response(200, json={"datablock": [1]}))

        open_call = respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/farm.php",
            params__contains={"mode": "pets_open_pack", "type": "1"},
        ).mock(return_value=httpx.Response(200, json={"datablock": [1]}))

        claimed = await helpers.handle_pet_parts(fm_data)
        assert claimed is True
        assert buy_call.called
        assert open_call.called


@pytest.mark.asyncio
async def test_pet_parts_already_claimed(fast_client: MFFGameClient):
    """Test pet parts does nothing when daily == 0."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    helpers = HelpersService(fast_client, stock_service=stock)

    fm_data = {"pets": {"daily": 0}}
    claimed = await helpers.handle_pet_parts(fm_data)
    assert claimed is False


