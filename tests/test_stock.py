import httpx
import pytest
import respx

from app.config import StockConfig
from app.services.catalog import build_catalog, parse_js_dict
from app.services.market_service import MarketService
from app.services.seed_dealer_service import SeedDealerService
from app.services.stock_service import StockService

SAMPLE_HTML = """
<html>
<script type="text/javascript">
var produkt_name = {1: 'Getreide', 2: 'Mais', 17: 'Karotte', 18: 'Gurke',};
var produkt_price = {1: '0.50', 2: '1.20', 17: '0.34', 18: '0.68',};
</script>
<script type="text/javascript" src="http://s1.myfreefarm.de/js/jsconstants_241014.js"></script>
</html>
"""

SAMPLE_JS_CONSTANTS = """
var produkt_x = {1: 1, 2: 1, 17: 1, 18: 2,};
var produkt_y = {1: 1, 2: 1, 17: 1, 18: 2,};
var produkt_category = {1: 'v', 2: 'v', 17: 'v', 18: 'v',};
"""


def test_catalog_parser():
    """Test parsing of JavaScript constants and HTML arrays into Product models."""
    names = parse_js_dict(SAMPLE_HTML, "produkt_name")
    assert names[17] == "Karotte"
    assert names[18] == "Gurke"

    catalog = build_catalog(SAMPLE_HTML, SAMPLE_JS_CONSTANTS)
    assert len(catalog) == 4
    assert catalog[17].name == "Karotte"
    assert catalog[17].price == 0.34
    assert not catalog[17].is_multi_tile

    assert catalog[18].name == "Gurke"
    assert catalog[18].is_multi_tile
    assert catalog[18].size_x == 2


def test_stock_update(fast_client):
    """Test updating in-memory inventory from updateblock.stock."""
    catalog = build_catalog(SAMPLE_HTML, SAMPLE_JS_CONSTANTS)
    service = StockService(fast_client)
    service.init_with_catalog(catalog)

    payload = {
        "updateblock": {
            "menue": {"bar": "1250.75"},
            "stock": {
                "stock": {
                    "1": {
                        "rack1": {
                            "item1": {"pid": "17", "amount": "450"},
                            "item2": {"pid": "1", "amount": "100"},
                        }
                    }
                },
                "tempstock": {
                    "17": {"5": "120"},
                },
            },
        }
    }

    service.update(payload)
    assert service.credits_kt == 1250.75
    assert service.get_product(17).amount == 450
    assert service.get_product(17).tmp_amount == 120
    assert service.get_available_amount(17) == 570
    assert service.get_available_amount(1) == 100
    assert service.get_available_amount(2) == 0


@pytest.mark.asyncio
async def test_market_service_get_offers(fast_client):
    """Test fetching and sorting player marketplace offers."""
    market = MarketService(fast_client)
    fast_client.rid = "test_rid"

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/city.php").mock(
            return_value=httpx.Response(
                200,
                json={
                    "datablock": [
                        1,
                        {
                            "offers": [
                                {"id": "101", "p": "17", "a": "200", "pr": "0.38"},
                                {"id": "102", "p": "17", "a": "100", "pr": "0.32"},
                                {"id": "103", "p": "1", "a": "500", "pr": "0.45"},
                            ]
                        },
                    ]
                },
            )
        )

        offers = await market.get_offers(pid=17)
        assert len(offers) == 2
        # Check cheapest offer is first
        assert offers[0].offer_id == 102
        assert offers[0].price == 0.32
        assert offers[0].amount == 100

        assert offers[1].offer_id == 101
        assert offers[1].price == 0.38


@pytest.mark.asyncio
async def test_market_service_buy(fast_client):
    """Test buying cheapest offers up to required amount."""
    market = MarketService(fast_client)
    fast_client.rid = "test_rid"

    with respx.mock:
        # 1. marketinit
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/city.php").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "datablock": [
                            1,
                            {
                                "offers": [
                                    {"id": "101", "p": "17", "a": "50", "pr": "0.30"},
                                    {"id": "102", "p": "17", "a": "100", "pr": "0.33"},
                                ]
                            },
                        ]
                    },
                ),
                # 2. marketbuyoffer 101
                httpx.Response(200, json={"datablock": [1]}),
                # 3. marketbuyoffer 102
                httpx.Response(200, json={"datablock": [1]}),
            ]
        )

        bought = await market.buy(pid=17, amount=120, max_price=0.35)
        # Should buy 50 from offer 101 and 70 from offer 102 = 120 total
        assert bought == 120


@pytest.mark.asyncio
async def test_seed_dealer_buy(fast_client):
    """Test buying seed from NPC dealer."""
    dealer = SeedDealerService(fast_client)
    fast_client.rid = "test_rid"

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/city.php").mock(
            return_value=httpx.Response(200, json={"datablock": [1]})
        )

        bought = await dealer.buy(pid=17, amount=300)
        assert bought == 300


@pytest.mark.asyncio
async def test_grasp_products_already_available(fast_client):
    """Test grasping when existing inventory is sufficient (no calls needed)."""
    catalog = build_catalog(SAMPLE_HTML, SAMPLE_JS_CONSTANTS)
    catalog[17].amount = 1000  # Plenty of carrots
    service = StockService(fast_client)
    service.init_with_catalog(catalog)

    success = await service.grasp_products([{"pid": 17, "amount": 200}])
    assert success is True


@pytest.mark.asyncio
async def test_grasp_products_fallback_to_seed_dealer(fast_client):
    """Test grasping crops when market is empty, falling back to seed dealer."""
    catalog = build_catalog(SAMPLE_HTML, SAMPLE_JS_CONSTANTS)
    catalog[17].amount = 50  # Only 50 in stock, need 100 + 500 buffer = 550 needed
    fast_client.rid = "test_rid"

    config = StockConfig(buffer_crops=500, min_credit_kt=100.0)
    service = StockService(fast_client, config=config)
    service.init_with_catalog(catalog)
    service.credits_kt = 500.0  # Sufficient balance

    with respx.mock:
        # Market has 0 offers
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/city.php").mock(
            side_effect=[
                # 1. marketinit
                httpx.Response(200, json={"datablock": [1, {"offers": []}]}),
                # 2. shopfire (Seed dealer buys remaining)
                httpx.Response(200, json={"datablock": [1]}),
            ]
        )
        # getfarms refresh call
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(
                200,
                json={"updateblock": {"stock": {"stock": {"1": {}}, "tempstock": {}}}},
            )
        )

        success = await service.grasp_products([{"pid": 17, "amount": 100}])
        assert success is True


@pytest.mark.asyncio
async def test_grasp_products_aborts_on_low_credit(fast_client):
    """Test that grasping aborts immediately if account credit is below min_credit_kt."""
    catalog = build_catalog(SAMPLE_HTML, SAMPLE_JS_CONSTANTS)
    catalog[17].amount = 0

    config = StockConfig(min_credit_kt=500.0)
    service = StockService(fast_client, config=config)
    service.init_with_catalog(catalog)
    service.credits_kt = 250.0  # Too low!

    success = await service.grasp_products([{"pid": 17, "amount": 100}])
    assert success is False
