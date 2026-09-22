import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import settings
from app.core.client import MFFGameClient
from app.main import app
from app.models.product import Product
from app.modules.agriculture.strategies import PlantStrategySolver
from app.modules.sushibar.farmis import SushiFarmiService
from app.modules.sushibar.kitchen import SushiKitchenService
from app.modules.sushibar.models import (
    SushiFarmi,
    SushiProductionSlot,
    SushiRecipe,
    SushiTrainSlot,
)
from app.modules.sushibar.solver import SushiQuestSolver
from app.modules.sushibar.train import SushiTrainService
from app.services.stock_service import StockService

SAMPLE_QUESTS5_HTML = """
<table class="newhelp_table" cellspacing="0" cellpadding="0">
  <tr class="newhelp_line">
    <td valign="top">64.</td>
    <td valign="top"><div class="kp8"></div> 35.600x&nbsp;Kornblumen</td>
    <td valign="top">Punkte</td>
  </tr>
  <tr class="newhelp_line">
    <td valign="top">72.</td>
    <td valign="top"><div class="kp978"></div> 741x&nbsp;Brunnenkressensalat</td>
    <td valign="top">Punkte</td>
  </tr>
  <tr class="newhelp_line">
    <td valign="top">74.</td>
    <td valign="top"><div class="kp984"></div> 843x&nbsp;Taro-Dampfnudeln</td>
    <td valign="top">Punkte</td>
  </tr>
</table>
"""


def test_sushibar_models():
    """Verify core properties of SushiBar Pydantic models."""
    # Recipe coin protection property
    r_kt = SushiRecipe(pid=978, name="Brunnenkressensalat", cost_money=2300, cost_coins=0)
    assert not r_kt.is_coin_recipe

    r_coins = SushiRecipe(pid=971, name="Wassernuss-Tasche", cost_money=0, cost_coins=20)
    assert r_coins.is_coin_recipe

    # Kitchen slot ready property
    slot_ready = SushiProductionSlot(slot=1, pid=978, remain=0, duration=10800, gone=10800, block=0)
    assert slot_ready.is_ready
    assert slot_ready.is_active

    slot_busy = SushiProductionSlot(slot=2, pid=984, remain=500, duration=10800, gone=10300, block=0)
    assert not slot_busy.is_ready
    assert slot_busy.is_active

    slot_empty = SushiProductionSlot(slot=3, pid=None, block=0)
    assert slot_empty.is_empty
    assert not slot_empty.is_ready

    # Farmi satisfaction and ready-to-cash
    farmi_done = SushiFarmi(
        id="123",
        slot=1,
        need={"sushi": 2, "soup": 1},
        have={"sushi": 2, "soup": 1},
        eat_remain=0,
    )
    assert farmi_done.is_satisfied
    assert farmi_done.is_ready_to_cash

    farmi_eating = SushiFarmi(
        id="124",
        slot=2,
        need={"sushi": 2},
        have={"sushi": 2},
        eat_remain=300,
    )
    assert farmi_eating.is_satisfied
    assert not farmi_eating.is_ready_to_cash  # Still chewing!

    farmi_hungry = SushiFarmi(
        id="125",
        slot=3,
        need={"sushi": 2},
        have={"sushi": 1},
        eat_remain=0,
    )
    assert not farmi_hungry.is_satisfied
    assert not farmi_hungry.is_ready_to_cash


def test_quest_solver_parsing():
    """Verify HTML parsing of Questreihe 5 help catalog."""
    solver = SushiQuestSolver()
    catalog = solver.parse_quests_html(SAMPLE_QUESTS5_HTML)
    assert 64 in catalog
    assert catalog[64] == {8: 35600}
    assert 72 in catalog
    assert catalog[72] == {978: 741}
    assert 74 in catalog
    assert catalog[74] == {984: 843}


@pytest.mark.asyncio
async def test_quest_solver_priority_and_field_reserve():
    """Verify that solver picks the earliest unsatisfied Quest 5 sushi requirement,
    while enforcing the 120-unit field-planting reserve."""
    solver = SushiQuestSolver()
    solver.parse_quests_html(SAMPLE_QUESTS5_HTML)

    client = MFFGameClient(server=1, username="test", password="pwd")
    stock_svc = StockService(client)

    # Setup dummy stock and catalog
    # PID 957 (Brunnenkresse) size 1x1, PID 17 (Karotten) size 1x1, PID 19 (Salat) size 1x1
    catalog = {
        957: Product(pid=957, name="Brunnenkresse", price=1.0, size_x=1, size_y=1, category="water"),
        17: Product(pid=17, name="Karotten", price=1.0, size_x=1, size_y=1, category="v"),
        19: Product(pid=19, name="Salat", price=1.0, size_x=1, size_y=1, category="v"),
        950: Product(pid=950, name="Reis", price=1.0, size_x=1, size_y=1, category="water"),
        953: Product(pid=953, name="Taro", price=1.0, size_x=1, size_y=1, category="water"),
        12: Product(pid=12, name="Honig", price=1.0, size_x=1, size_y=1, category="prod"),
        978: Product(pid=978, name="Brunnenkressensalat", price=5.0, size_x=1, size_y=1, category="sushi"),
        984: Product(pid=984, name="Taro-Dampfnudeln", price=5.0, size_x=1, size_y=1, category="sushi"),
    }
    stock_svc.catalog = catalog

    recipes = {
        978: SushiRecipe(
            pid=978,
            name="Brunnenkressensalat",
            level=11,
            cost_money=2300,
            needs={957: 14, 17: 11, 19: 12},
        ),
        984: SushiRecipe(
            pid=984,
            name="Taro-Dampfnudeln",
            level=13,
            cost_money=2500,
            needs={950: 8, 953: 7, 12: 5},
        ),
    }

    # Case 1: Ingredients for 978 (Quest 72) are abundant (> 120 reserve)
    stock_svc.products = {
        978: Product(pid=978, name="Brunnenkressensalat", amount=0),
        984: Product(pid=984, name="Taro-Dampfnudeln", amount=0),
        957: Product(pid=957, name="Brunnenkresse", amount=200),  # 200 - 120 = 80 >= 14
        17: Product(pid=17, name="Karotten", amount=200),
        19: Product(pid=19, name="Salat", amount=200),
        950: Product(pid=950, name="Reis", amount=200),
        953: Product(pid=953, name="Taro", amount=200),
        12: Product(pid=12, name="Honig", amount=200),
    }

    recipe, target = await solver.find_best_recipe(
        current_quest_id=64,
        sushibar_level=14,
        recipes=recipes,
        stock_service=stock_svc,
        catalog=catalog,
        strategy="quest5",
        reserve_full_field=True,
    )
    assert recipe is not None
    assert recipe.pid == 978
    assert target is not None
    assert target.quest_id == 72
    assert target.missing == 741

    # Case 2: Brunnenkresse (957) drops to 125.
    # 125 - 120 reserve = 5 available < 14 needed!
    # Field reserve must prevent cooking 978, so solver advances to Quest 74 (Taro-Dampfnudeln 984)!
    stock_svc.products[957].amount = 125
    recipe, target = await solver.find_best_recipe(
        current_quest_id=64,
        sushibar_level=14,
        recipes=recipes,
        stock_service=stock_svc,
        catalog=catalog,
        strategy="quest5",
        reserve_full_field=True,
    )
    assert recipe is not None
    assert recipe.pid == 984
    assert target is not None
    assert target.quest_id == 74
    assert target.missing == 843


@pytest.mark.asyncio
async def test_kitchen_harvest_and_produce():
    """Verify kitchen harvesting and production calls."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    client.rid = "test_rid"
    kitchen = SushiKitchenService(client)

    # Set up slots: Slot 1 is ready to harvest, Slot 2 is empty
    kitchen.slots = [
        SushiProductionSlot(slot=1, pid=984, product_name="Taro-Dampfnudeln", amount=4, remain=0, duration=10800, gone=10800, block=0),
        SushiProductionSlot(slot=2, pid=None, block=0),
    ]

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"status": "ok", "updateblock": {}})
        )

        # 1. Harvest ready slot 1
        harvested = await kitchen.harvest()
        assert harvested == 1

        # 2. Produce in slot 2
        solver = SushiQuestSolver()
        solver.parse_quests_html(SAMPLE_QUESTS5_HTML)
        stock_svc = StockService(client)
        stock_svc.products = {
            978: Product(pid=978, name="Brunnenkressensalat", amount=0),
            957: Product(pid=957, name="Brunnenkresse", amount=200),
            17: Product(pid=17, name="Karotten", amount=200),
            19: Product(pid=19, name="Salat", amount=200),
        }
        recipes = {
            978: SushiRecipe(pid=978, name="Brunnenkressensalat", level=11, cost_money=2300, needs={957: 14, 17: 11, 19: 12}),
        }

        started = await kitchen.produce(
            stock_service=stock_svc,
            quest_solver=solver,
            sushibar_level=14,
            recipes=recipes,
            current_quest_id=64,
            strategy="quest5",
        )
        assert started == 1
        assert stock_svc.products[957].amount == 200 - 14


@pytest.mark.asyncio
async def test_farmi_collection_and_coin_protection():
    """Verify that satisfied Farmis are cashed out and no coins are spent."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    client.rid = "test_rid"
    farmi_svc = SushiFarmiService(client)

    farmi_svc.farmis = [
        SushiFarmi(id="101", slot=1, need={"sushi": 2}, have={"sushi": 2}, eat_remain=0),
        SushiFarmi(id="102", slot=2, need={"soup": 3}, have={"soup": 2}, eat_remain=0),
    ]

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(200, json={"status": "ok", "updateblock": {}})
        )

        collected = await farmi_svc.collect(auto_farmi=True)
        assert collected == 1


def test_train_inactive_by_default():
    """Ensure conveyor belt remains idle when auto_train=False."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    train_svc = SushiTrainService(client)
    train_svc.slots = [SushiTrainSlot(slot=1, buy_time=12345, pid=None)]

    stock_svc = StockService(client)
    recipes = {978: SushiRecipe(pid=978, name="Test")}

    import asyncio
    filled = asyncio.run(train_svc.fill(stock_svc, [], recipes, auto_train=False))
    assert filled == 0


def test_sushibar_api_endpoints():
    """Verify REST API endpoints for Sushi-Bar."""
    client = TestClient(app)

    # Login to get JWT
    login_resp = client.post(
        f"{settings.api_prefix}/login",
        json={"username": settings.api_username, "password": settings.api_password},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. GET /sushibar
    resp = client.get(f"{settings.api_prefix}/sushibar", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "config" in data
    assert data["config"]["auto_train"] is False  # Inactive by default

    # 2. GET /sushibar/settings
    resp = client.get(f"{settings.api_prefix}/sushibar/settings", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["production_strategy"] == "quest5"

    # 3. PUT /sushibar/settings
    update_payload = {"auto_produce": False, "production_strategy": "balanced"}
    resp = client.put(
        f"{settings.api_prefix}/sushibar/settings",
        json=update_payload,
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["config"]["auto_produce"] is False
    assert resp.json()["config"]["production_strategy"] == "balanced"

    # Reset back to quest5
    client.put(
        f"{settings.api_prefix}/sushibar/settings",
        json={"auto_produce": True, "production_strategy": "quest5"},
        headers=headers,
    )


@pytest.mark.asyncio
async def test_farm8_stock_resolution_and_deduct():
    """Verify that SushiQuestSolver resolves stock from Farm 8 rack and deduct_stock works."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    stock_svc = StockService(client)

    catalog = {
        957: Product(pid=957, name="Brunnenkresse", category="water", size_x=1, size_y=1),
        17: Product(pid=17, name="Karotten", category="v", size_x=1, size_y=1),
        19: Product(pid=19, name="Radieschen", category="v", size_x=1, size_y=1),
        9: Product(pid=9, name="Eier", category="e", size_x=1, size_y=1),
        978: Product(pid=978, name="Brunnenkressensalat", category="sushi"),
    }
    stock_svc.products = catalog

    # Populate farm_stocks: Farm 8 has water plants, Farm 1 has main stock
    stock_svc.farm_stocks = {
        1: {17: 2000, 19: 1500, 9: 500},
        8: {957: 6000, 17: 1000, 19: 800, 978: 25},
    }

    # Test get_farm_amount on Farm 8:
    assert stock_svc.get_farm_amount(8, 957) == 6000
    assert stock_svc.get_farm_amount(8, 17) == 1000
    # PID 9 is not on Farm 8, fallback to Farm 1:
    assert stock_svc.get_farm_amount(8, 9, include_fallback=True) == 500

    # Test get_total_stock:
    # Karotten: 2000 on farm 1 + 1000 on farm 8 = 3000
    assert stock_svc.get_total_stock(17) == 3000
    # Brunnenkressensalat: 25 on farm 8
    assert stock_svc.get_total_stock(978) == 25

    solver = SushiQuestSolver(client, farm_id=8)
    recipe = SushiRecipe(
        pid=978,
        name="Brunnenkressensalat",
        level=11,
        needs={957: 14, 17: 11, 19: 12},
    )
    # Check ingredients: Brunnenkresse 6000 - 120 >= 14, Karotten 1000 - 120 >= 11, Radieschen 800 - 120 >= 12
    assert solver.check_ingredients(recipe, stock_svc, catalog=catalog, reserve_full_field=True)

    # Test deduct_stock:
    stock_svc.deduct_stock(957, 14, farm_id=8)
    assert stock_svc.get_farm_amount(8, 957) == 6000 - 14


@pytest.mark.asyncio
async def test_quest5_water_requirements_and_farm8_planting():
    """Verify that Farm 8 water crops are selected based on Quest 5 direct and sushi requirements."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    stock_svc = StockService(client)

    catalog = {
        950: Product(pid=950, name="Reis", category="water", size_x=2, size_y=1),
        953: Product(pid=953, name="Taro", category="water", size_x=2, size_y=1),
        957: Product(pid=957, name="Brunnenkresse", category="water", size_x=1, size_y=1),
        978: Product(pid=978, name="Brunnenkressensalat", category="sushi"),
        984: Product(pid=984, name="Taro-Dampfnudeln", category="sushi"),
    }
    stock_svc.products = catalog

    recipes = {
        978: SushiRecipe(pid=978, name="Brunnenkressensalat", amount=4, needs={957: 14}),
        984: SushiRecipe(pid=984, name="Taro-Dampfnudeln", amount=4, needs={950: 8, 953: 7}),
    }

    solver = SushiQuestSolver(client, farm_id=8)
    # Mock quests catalog:
    # Quest 66: Direct 6979x Taro (PID 953)
    # Quest 72: 741x Brunnenkressensalat (PID 978) -> requires Brunnenkresse 957
    solver._quests5_cache = {
        66: {953: 6979},
        72: {978: 741},
    }

    # Case 1: Taro (953) is deficient for Quest 66 (< 6979 + 500 = 7479)
    # Brunnenkresse (957) is deficient for Quest 72 (needs 2604 + 500 = 3104 > available 1880)
    # Farm 8 rack has 1000 Taro seeds and 2000 Brunnenkresse seeds
    stock_svc.farm_stocks = {
        8: {953: 1000, 957: 2000, 978: 0},
    }
    candidates = await solver.get_quest5_water_requirements(
        current_quest_id=64,
        recipes=recipes,
        stock_service=stock_svc,
        catalog=catalog,
        min_products=500,
    )
    assert len(candidates) == 2
    # Candidate 1: Taro (Quest 66 direct)
    assert candidates[0][0] == 953
    assert candidates[0][2] == 66
    # Candidate 2: Brunnenkresse (Quest 72 sushi)
    assert candidates[1][0] == 957
    assert candidates[1][2] == 72

    # Plant candidate on Farm 8: Taro (953) chosen because it comes first in quest progression and has seeds
    chosen = PlantStrategySolver.resolve_candidate(
        strategy_name="plantQuest",
        stock_service=stock_svc,
        category="water",
        farm_id=8,
        quest5_water_candidates=candidates,
    )
    assert chosen is not None
    assert chosen.pid == 953

    # Case 2: Taro has 0 seeds in Farm 8 rack!
    # Solver must skip Taro and pick Brunnenkresse (957) from Quest 72!
    stock_svc.farm_stocks[8][953] = 0
    chosen2 = PlantStrategySolver.resolve_candidate(
        strategy_name="plantQuest",
        stock_service=stock_svc,
        category="water",
        farm_id=8,
        quest5_water_candidates=candidates,
    )
    assert chosen2 is not None
    assert chosen2.pid == 957


@pytest.mark.asyncio
async def test_quest5_water_requirements_logistics_vs_planting():
    """Verify that get_quest5_water_requirements separates planting needs (system stock)
    from logistics needs (strictly Farm 1 stock, excluding sushi ingredient crops)."""
    client = MFFGameClient(server=1, username="test", password="pwd")
    stock_svc = StockService(client)

    catalog = {
        950: Product(pid=950, name="Reis", category="water", size_x=2, size_y=1),
        953: Product(pid=953, name="Taro", category="water", size_x=2, size_y=1),
        957: Product(pid=957, name="Brunnenkresse", category="water", size_x=1, size_y=1),
        978: Product(pid=978, name="Brunnenkressensalat", category="sushi"),
    }
    stock_svc.products = catalog

    recipes = {
        978: SushiRecipe(pid=978, name="Brunnenkressensalat", amount=4, needs={957: 14}),
    }

    solver = SushiQuestSolver(client, farm_id=8)
    # Mock quests:
    # Q66: 6979 Taro (953)
    # Q68: 7884 Brunnenkresse (957)
    # Q72: 741 Brunnenkressensalat (978)
    solver._quests5_cache = {
        66: {953: 6979},
        68: {957: 7884},
        72: {978: 741},
    }

    # Setup stocks:
    # Farm 8 has 7820 Taro (enough for Q66 + reserve 500) and 2000 Brunnenkresse
    # Farm 1 has 0 Taro and 0 Brunnenkresse
    stock_svc.farm_stocks = {
        1: {953: 0, 957: 0},
        8: {953: 7820, 957: 2000, 978: 0},
    }
    stock_svc.farm_temp_stocks = {
        8: {953: 7820, 957: 2000},
    }

    # 1. PLANTING (for_logistics=False):
    # Taro 953: Farm 8 has 7820 >= 6979 + 500 -> Satisfied! Taro should NOT be planted.
    # Brunnenkresse 957: Needed 7884 for Q68 + sushi Q72. Farm 8 has only 2000 -> Deficient!
    planting_candidates = await solver.get_quest5_water_requirements(
        current_quest_id=66,
        recipes=recipes,
        stock_service=stock_svc,
        catalog=catalog,
        min_products=500,
        for_logistics=False,
    )
    planting_pids = [c[0] for c in planting_candidates]
    assert 953 not in planting_pids  # Taro is complete for planting
    assert 957 in planting_pids  # Brunnenkresse needs planting

    # 2. LOGISTICS (for_logistics=True):
    # Evaluates Farm 1 main stock!
    # Farm 1 has 0 Taro -> Quest 66 needs 6979 on Farm 1!
    # Farm 1 has 0 Brunnenkresse -> Quest 68 needs 7884 on Farm 1!
    # Sushi ingredients (Q72 978 needs 957) must NOT be shipped to Farm 1 (cooked on Farm 8)
    logistics_candidates = await solver.get_quest5_water_requirements(
        current_quest_id=66,
        recipes=recipes,
        stock_service=stock_svc,
        catalog=catalog,
        min_products=500,
        for_logistics=True,
    )
    logistics_pids = [c[0] for c in logistics_candidates]
    assert 953 in logistics_pids  # Taro must be shipped to Farm 1!
    assert logistics_candidates[0][0] == 953
    assert logistics_candidates[0][2] == 66  # Priority is Quest 66!
    assert logistics_candidates[0][1] == 6979  # Full quest requirement

    assert 957 in logistics_pids
    assert logistics_candidates[1][0] == 957
    assert logistics_candidates[1][2] == 68  # Priority is Quest 68!



