import httpx
import pytest
import respx

from app.core.client import MFFGameClient
from app.models.forestry import ForestryProduct
from app.modules.forestry.factory import Carpentry, Sawmill
from app.modules.forestry.farmis import ForestryFarmis
from app.modules.forestry.forestry_service import ForestryService
from app.modules.forestry.forrest import Forrest


@pytest.mark.asyncio
async def test_forrest_cut_plant_water(fast_client: MFFGameClient):
    """Test Forrest cutting ripe trees, replanting seedlings, and watering."""
    fast_client.rid = "test_rid"
    forrest = Forrest(fast_client)

    raw_trees = [
        {"position": 1, "productid": 1, "remain": 0, "waterremain": 0},  # Ripe, needs cut
        {"position": 2, "productid": None, "remain": 0, "waterremain": 0},  # Empty slot
        {"position": 3, "productid": 2, "remain": 500, "waterremain": 0},  # Growing, needs water
    ]
    forrest.update(raw_trees)

    assert forrest.has_ready_trees is True
    assert forrest.has_empty_slots is True
    assert forrest.needs_watering is True

    products = {
        51: ForestryProduct(pid=51, name="Fichte Stamm", category=2, amount=20),
        1: ForestryProduct(pid=1, name="Fichte Setzling", category=1, amount=10, produces=51),
    }

    with respx.mock:
        # Mock cropall
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/forestry.php").mock(
            side_effect=[
                httpx.Response(200, json={"datablock": [1, []]}),  # cropall
                httpx.Response(200, json={"datablock": [1, []]}),  # autoplant
                httpx.Response(200, json={"datablock": [1, []]}),  # water
            ]
        )

        assert await forrest.cut() is True
        assert await forrest.plant(products) == 1
        assert await forrest.water() is True


@pytest.mark.asyncio
async def test_forrest_plant_chooses_lowest_trunk_stock(fast_client: MFFGameClient):
    """Verify that Forrest selects the seedling corresponding to the lowest trunk in inventory."""
    fast_client.rid = "test_rid"
    forrest = Forrest(fast_client)
    forrest.trees[0].productid = None  # Force empty slot

    products = {
        51: ForestryProduct(pid=51, name="Fichtenstamm", category=2, amount=100),
        1: ForestryProduct(pid=1, name="Fichtensetzling", category=1, produces=51),
        52: ForestryProduct(pid=52, name="Kiefernstamm", category=2, amount=15),
        2: ForestryProduct(pid=2, name="Kiefernsetzling", category=1, produces=52),
    }

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/forestry.php").mock(
            return_value=httpx.Response(200, json={"datablock": [1, []]})
        )
        # Kiefer trunk (52) is lowest (15 < 100), so Seedling 2 should be chosen
        chosen_pid = await forrest.plant(products)
        assert chosen_pid == 2


@pytest.mark.asyncio
async def test_sawmill_harvest_and_produce(fast_client: MFFGameClient):
    """Test Sawmill slot harvesting and starting new board production."""
    fast_client.rid = "test_rid"
    sawmill = Sawmill(fast_client)

    building_data = {
        "slots": {
            "1": {"productid": 55, "remain": 0, "ready": 1},  # Finished
            "2": {"productid": 55, "remain": 1000, "ready": 0},  # Busy
        }
    }
    sawmill.update(building_data)
    assert sawmill.slots[1].is_finished is True
    assert sawmill.slots[2].busy is True

    products = {
        51: ForestryProduct(pid=51, name="Fichtenstamm", category=2, amount=10),
        55: ForestryProduct(
            pid=55,
            name="Fichtenbrett",
            category=3,
            amount=2,
            required_products=[(51, 2)],
        ),
    }

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/forestry.php").mock(
            side_effect=[
                httpx.Response(200, json={"datablock": [1, []]}),  # cropproduction
                httpx.Response(200, json={"datablock": [1, []]}),  # startproduction
            ]
        )

        harvested = await sawmill.harvest()
        assert harvested == 1
        assert sawmill.slots[1].busy is False

        started = await sawmill.produce_available(products)
        assert started == 1
        # Check ingredient was deducted
        assert products[51].amount == 8


@pytest.mark.asyncio
async def test_carpentry_harvest_and_produce(fast_client: MFFGameClient):
    """Test Carpentry slot harvesting and starting new furniture production."""
    fast_client.rid = "test_rid"
    carpentry = Carpentry(fast_client)

    building_data = {
        "slots": {
            "1": None,  # Free
            "2": {"productid": 80, "remain": 3600, "ready": 0},  # Busy
        }
    }
    carpentry.update(building_data)

    products = {
        55: ForestryProduct(pid=55, name="Fichtenbrett", category=3, amount=5),
        80: ForestryProduct(
            pid=80,
            name="Holztisch",
            category=4,
            amount=0,
            required_products=[(55, 3)],
        ),
    }

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/forestry.php").mock(
            return_value=httpx.Response(200, json={"datablock": [1, []]})
        )

        started = await carpentry.produce_available(products)
        assert started == 1
        assert products[55].amount == 2


@pytest.mark.asyncio
async def test_forestry_farmis_serve(fast_client: MFFGameClient):
    """Test serving visitor customers at the forest hut."""
    fast_client.rid = "test_rid"
    farmis = ForestryFarmis(fast_client)

    raw_farmis = [
        {
            "farmiid": 101,
            "position": 1,
            "points": 500,
            "price": 45.0,
            "products": [{"product": 55, "amount": 2}],
        }
    ]
    farmis.update(raw_farmis)
    assert len(farmis.farmis) == 1

    products = {
        55: ForestryProduct(pid=55, name="Fichtenbrett", category=3, amount=5),
    }

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/forestry.php").mock(
            return_value=httpx.Response(200, json={"datablock": [1, []]})
        )

        served = await farmis.serve_available(products)
        assert served == 1
        assert products[55].amount == 3  # 5 - 2 = 3


@pytest.mark.asyncio
async def test_forestry_service_full_cycle(fast_client: MFFGameClient):
    """Test full cycle execution of ForestryService."""
    fast_client.rid = "test_rid"
    service = ForestryService(fast_client)

    init_response = {
        "datablock": [
            1,
            [{"position": 1, "productid": 1, "remain": 0, "waterremain": 0}],  # Trees
            {"1": {"slots": {}}, "2": {"slots": {}}},  # Factories
            {},
            {  # Catalog
                "1": {
                    "1": [0, 0, 1, 1, 0, 51, 0, 0, 0, "Fichte Setzling"],
                    "51": [0, 0, 1, 2, 0, 0, 0, 0, 0, "Fichte Stamm"],
                }
            },
            [],  # Farmis
        ],
        "updateblock": {"forestry_stock": {"51": 25}},
    }

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/forestry.php").mock(
            side_effect=[
                httpx.Response(200, json=init_response),  # initforestry
                httpx.Response(200, json={"datablock": [1, []]}),  # cropall
                httpx.Response(200, json={"datablock": [1, []]}),  # autoplant
                httpx.Response(200, json={"datablock": [1, []]}),  # water
            ]
        )

        res = await service.serve()
        assert res.get("datablock", [0])[0] == 1
        assert 51 in service.products
        assert service.products[51].amount == 25


@pytest.mark.asyncio
async def test_carpentry_demand_driven_only(fast_client: MFFGameClient):
    """Verify Carpentry ONLY produces items requested by waiting Farmis and ignores unrequested items."""
    fast_client.rid = "test_rid"
    service = ForestryService(fast_client)

    # Farmi requests PID 104 (Holzrechen)
    raw_farmis = [
        {"farmiid": 1, "position": 1, "products": [{"product": 104, "amount": 1}]}
    ]
    service.farmis.update(raw_farmis)

    # In stock: Bretter (PID 55) available to make anything.
    # PID 109 (Futtertrog) and PID 122 (Mast) have 0 stock, PID 104 (Holzrechen) has 0 stock.
    service.products = {
        55: ForestryProduct(pid=55, name="Fichtenbrett", category=3, amount=50),
        104: ForestryProduct(
            pid=104,
            name="Holzrechen",
            category=4,
            amount=0,
            required_products=[(55, 2)],
        ),
        109: ForestryProduct(
            pid=109,
            name="Futtertrog",
            category=4,
            amount=0,
            required_products=[(55, 3)],
        ),
        122: ForestryProduct(
            pid=122,
            name="Mast",
            category=4,
            amount=0,
            required_products=[(55, 4)],
        ),
    }

    demands = service.farmis.get_missing_demands(service.products)
    assert demands == {104: 1}  # Only 104 is demanded!

    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/forestry.php").mock(
            return_value=httpx.Response(200, json={"datablock": [1, []]})
        )
        started, _subs = await service.carpentry.produce_orders(service.products, demands)
        # Should only start PID 104, exactly 1 slot
        assert started == 1
        assert service.carpentry.slots[1].productid == 104
        assert service.carpentry.slots[2].busy is False
        # Unrequested products (109, 122) MUST NOT be produced!
        assert service.carpentry.slots[2].productid is None


@pytest.mark.asyncio
async def test_carpentry_stays_idle_without_demand(fast_client: MFFGameClient):
    """Verify Carpentry stays completely idle if no Farmi needs furniture."""
    fast_client.rid = "test_rid"
    service = ForestryService(fast_client)
    service.farmis.update([])  # No farmis

    service.products = {
        55: ForestryProduct(pid=55, name="Fichtenbrett", category=3, amount=50),
        109: ForestryProduct(
            pid=109,
            name="Futtertrog",
            category=4,
            amount=0,
            required_products=[(55, 3)],
        ),
    }

    demands = service.farmis.get_missing_demands(service.products)
    assert demands == {}

    started, _subs = await service.carpentry.produce_orders(service.products, demands)
    assert started == 0
    assert service.carpentry.slots[1].busy is False
    assert service.carpentry.slots[2].busy is False
