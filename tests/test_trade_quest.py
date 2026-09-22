import httpx
import pytest
import respx

from app.config import TradeConfig, TradeRule
from app.core.client import MFFGameClient
from app.models.product import Product
from app.modules.agriculture.strategies import PlantStrategySolver
from app.services.quest_service import QuestService
from app.services.stock_service import StockService
from app.services.trade_service import TradeService


@pytest.mark.asyncio
async def test_trade_service_sells_surplus(fast_client: MFFGameClient):
    """Test TradeService identifies surplus above reserve, calculates underbid price, and creates market offer."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    stock.credits_kt = 1000.0

    carrot = Product(pid=17, name="Karotte", price=0.34, category="v", amount=1500)
    stock.init_with_catalog({17: carrot})

    config = TradeConfig(
        enabled=True,
        min_credit_kt=100.0,
        default_reserve=1000,
        underbid_offset_kt=0.01,
        auto_sell_surplus=True,
        sell_category_v=True,
        exclude_categories=[],
        sell_rules=[TradeRule(pid=17, min_reserve=1000, sell_batch=100)],
    )
    trade_svc = TradeService(fast_client, stock_service=stock, config=config)

    with respx.mock:
        # Mock marketinit with lowest offer at 0.50 kT
        market_data = {
            "datablock": [
                1,
                {"offers": [{"id": 999, "p": 17, "a": 200, "pr": 0.50}]},
            ]
        }
        create_offer_data = {
            "datablock": [1],
            "updateblock": {
                "stock": {
                    "stock": {"1": {"1": {"1": {"pid": 17, "amount": 1000}}}},
                    "tempstock": {},
                },
                "menue": {"bar": "990.00"},
            },
        }

        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/city.php").mock(
            side_effect=[
                httpx.Response(200, json=market_data),  # marketinit
                httpx.Response(200, json=create_offer_data),  # marketcreateoffer
            ]
        )

        offers_count = await trade_svc.sell_surplus()
        assert offers_count == 1
        # Stock should now be updated to 1000
        assert stock.get_product(17).amount == 1000


@pytest.mark.asyncio
async def test_trade_service_excludes_category_v(fast_client: MFFGameClient):
    """Test TradeService strictly refuses to sell category 'v' products on the market."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    stock.credits_kt = 1000.0

    carrot = Product(pid=17, name="Karotte", price=0.34, category="v", amount=1500)
    wool = Product(pid=25, name="Wolle", price=15.0, category="t", amount=1500)
    stock.init_with_catalog({17: carrot, 25: wool})

    config = TradeConfig(
        enabled=True,
        min_credit_kt=100.0,
        default_reserve=1000,
        underbid_offset_kt=0.01,
        auto_sell_surplus=True,
        sell_category_v=False,  # Exclude category 'v'
        exclude_categories=["v"],
    )
    trade_svc = TradeService(fast_client, stock_service=stock, config=config)

    with respx.mock:
        market_data = {
            "datablock": [
                1,
                {"offers": [{"id": 999, "p": 25, "a": 200, "pr": 15.50}]},
            ]
        }
        create_offer_data = {
            "datablock": [1],
            "updateblock": {
                "stock": {
                    "stock": {"1": {"1": {"1": {"pid": 25, "amount": 1000}, "2": {"pid": 17, "amount": 1500}}}},
                    "tempstock": {},
                },
                "menue": {"bar": "990.00"},
            },
        }

        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/city.php").mock(
            side_effect=[
                httpx.Response(200, json=market_data),  # marketinit for Wolle
                httpx.Response(200, json=create_offer_data),  # marketcreateoffer for Wolle
            ]
        )

        offers_count = await trade_svc.sell_surplus()
        # Only Wolle (category 't') should be sold, Karotte (category 'v') must be skipped!
        assert offers_count == 1
        assert stock.get_product(25).amount == 1000
        # Karotte must be untouched
        assert stock.get_product(17).amount == 1500

    # Also verify create_offer directly rejects category 'v'
    rejected = await trade_svc.create_offer(17, 100, 0.5)
    assert rejected is False


@pytest.mark.asyncio
async def test_trade_service_respects_min_credit(fast_client: MFFGameClient):
    """Test TradeService halts selling when account balance is below min_credit_kt."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    stock.credits_kt = 50.0  # Below min 100.0

    carrot = Product(pid=17, name="Karotte", price=0.34, category="v", amount=5000)
    stock.init_with_catalog({17: carrot})

    config = TradeConfig(enabled=True, min_credit_kt=100.0, default_reserve=1000)
    trade_svc = TradeService(fast_client, stock_service=stock, config=config)

    offers_count = await trade_svc.sell_surplus()
    assert offers_count == 0


@pytest.mark.asyncio
async def test_quest_service_parses_status(fast_client: MFFGameClient):
    """Test QuestService parsing quest requirements and evaluating completeness."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    stock.init_with_catalog(
        {
            17: Product(pid=17, name="Karotte", price=0.34, category="v", amount=100),
            18: Product(pid=18, name="Gurke", price=0.48, category="v", amount=300),
        }
    )

    quest_svc = QuestService(fast_client, stock_service=stock)

    raw_quest = {
        "title": "Karottensalat",
        "data": [
            None,
            [{"17": 250, "18": 150}],  # 250 carrots, 150 cucumbers needed
        ],
    }

    status = quest_svc.parse_quest_data(quest_nr=5, quest_data=raw_quest)
    assert status.quest_nr == 5
    assert len(status.requirements) == 2

    carrot_req = next(r for r in status.requirements if r.pid == 17)
    assert carrot_req.amount_needed == 250
    assert carrot_req.current_stock == 100
    assert carrot_req.missing == 150

    cucumber_req = next(r for r in status.requirements if r.pid == 18)
    assert cucumber_req.amount_needed == 150
    assert cucumber_req.current_stock == 300
    assert cucumber_req.missing == 0

    assert status.is_ready is False


@pytest.mark.asyncio
async def test_quest_service_integration_with_plant_strategy(fast_client: MFFGameClient):
    """Verify that PlantStrategySolver prioritizes quest deficit crop over general min stock."""
    stock = StockService(fast_client)
    carrot = Product(pid=17, name="Karotte", price=0.34, category="v", amount=150)
    cucumber = Product(pid=18, name="Gurke", price=0.48, category="v", amount=50)
    stock.init_with_catalog({17: carrot, 18: cucumber})

    # Under plantMin, cucumber (50) would win because 50 < 150
    min_candidate = PlantStrategySolver.resolve_candidate("plantMin", stock_service=stock)
    assert min_candidate.pid == 18

    # Under plantQuest with 500 Carrots needed, Carrot has a huge deficit and should win
    quest_reqs = {17: 500}
    quest_candidate = PlantStrategySolver.resolve_candidate(
        "plantQuest",
        stock_service=stock,
        quest_requirements=quest_reqs,
    )
    assert quest_candidate.pid == 17


@pytest.mark.asyncio
async def test_trade_service_disabled(fast_client: MFFGameClient):
    """Test TradeService respects disabled flag and does not create any offers."""
    stock = StockService(fast_client)
    config = TradeConfig(enabled=False)
    trade_svc = TradeService(fast_client, stock_service=stock, config=config)

    res = await trade_svc.serve()
    assert res == {"enabled": False, "offers_created": 0}


@pytest.mark.asyncio
async def test_quest_service_multi_campaign_requirements(fast_client: MFFGameClient):
    """Test QuestService aggregates requirements from multiple parallel active campaigns (e.g. Campaign 4 & 5)."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    stock.init_with_catalog(
        {
            708: Product(pid=708, name="Melisse", category="alpin", amount=0),
            957: Product(pid=957, name="Brunnenkresse", category="water", amount=0),
        }
    )

    quest_svc = QuestService(fast_client, stock_service=stock)

    mock_response = {
        "datablock": 1,
        "updateblock": {
            "queststatus": {
                "main": {
                    "4": {
                        "questid": 91,
                        "data": {
                            "1": [{"708": 36619}],
                            "6": "Naturschutzquest",
                            "8": 172800,
                        },
                    },
                    "5": {
                        "questid": 68,
                        "data": {
                            "1": [{"957": 7884}],
                            "6": "Wasserschutzquest",
                            "8": 172800,
                        },
                    },
                }
            }
        },
    }

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/quest.php").mock(
            return_value=httpx.Response(200, json=mock_response)
        )
        status = await quest_svc.fetch_quest_status()
        assert status is not None
        assert len(quest_svc.active_quests) == 2
        assert 4 in quest_svc.active_quests
        assert 5 in quest_svc.active_quests

        # Combined requirements across all campaigns
        reqs = quest_svc.get_requirements_dict()
        assert reqs[708] == 36619
        assert reqs[957] == 7884

        # Specific campaign requirements
        assert quest_svc.get_requirements_dict(campaign_id=5) == {957: 7884}
        assert quest_svc.get_requirements_dict(campaign_id=4) == {708: 36619}


@pytest.mark.asyncio
async def test_trade_service_aborts_on_market_full(fast_client: MFFGameClient):
    """Test TradeService immediately breaks loop when server rejects offer with 20 offers limit."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    stock.credits_kt = 1000.0

    # Two products with surplus in eligible category 't'
    wool = Product(pid=25, name="Wolle", price=15.0, category="t", amount=2000)
    milk = Product(pid=26, name="Milch", price=10.0, category="t", amount=2000)
    stock.init_with_catalog({25: wool, 26: milk})

    config = TradeConfig(
        enabled=True,
        min_credit_kt=100.0,
        default_reserve=1000,
        auto_sell_surplus=True,
        sell_category_v=False,
        exclude_categories=["v"],
    )
    trade_svc = TradeService(fast_client, stock_service=stock, config=config)

    with respx.mock:
        market_data = {
            "datablock": [
                1,
                {"offers": [{"id": 999, "p": 25, "a": 200, "pr": 15.50}]},
            ]
        }
        # Server rejects offer because 20 offers are already active
        reject_response = {
            "0": 0,
            "1": "Mehr als 20 Angebote gleichzeitig am Markt sind nicht möglich.",
        }

        mock_route = respx.get(url__startswith="https://s1.myfreefarm.de/ajax/city.php").mock(
            side_effect=[
                httpx.Response(200, json=market_data),  # marketinit for Wolle (PID 25)
                httpx.Response(200, json=reject_response),  # marketcreateoffer for Wolle (PID 25)
            ]
        )

        offers_count = await trade_svc.sell_surplus()

        # Should have stopped after Wolle rejection
        assert offers_count == 0
        assert trade_svc.market_full is True
        # Total calls should be exactly 2 (marketinit & marketcreateoffer for Wolle). Milch (PID 26) must NOT be touched.
        assert mock_route.call_count == 2
        for call in mock_route.calls:
            assert "pid=26" not in str(call.request.url)


@pytest.mark.asyncio
async def test_trade_service_aborts_on_api_error(fast_client: MFFGameClient):
    """Test TradeService immediately breaks loop when API fails with plain text 'failed'."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    stock.credits_kt = 1000.0

    wool = Product(pid=25, name="Wolle", price=15.0, category="t", amount=2000)
    milk = Product(pid=26, name="Milch", price=10.0, category="t", amount=2000)
    stock.init_with_catalog({25: wool, 26: milk})

    config = TradeConfig(
        enabled=True,
        min_credit_kt=100.0,
        default_reserve=1000,
        auto_sell_surplus=True,
        sell_category_v=False,
    )
    trade_svc = TradeService(fast_client, stock_service=stock, config=config)

    with respx.mock:
        # City endpoint returns non-JSON "failed", causing UpstreamAPIError
        mock_route = respx.get(url__startswith="https://s1.myfreefarm.de/ajax/city.php").mock(
            return_value=httpx.Response(200, text="failed")
        )

        offers_count = await trade_svc.sell_surplus()

        assert offers_count == 0
        assert trade_svc.market_full is True
        # Should have failed on Wolle marketinit and immediately halted
        assert mock_route.call_count == 1
        assert "mode=marketinit" in str(mock_route.calls[0].request.url)



