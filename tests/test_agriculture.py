from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from app.config import FarmStrategyConfig
from app.models.product import Product
from app.modules.agriculture.field import Field
from app.modules.agriculture.strategies import PlantStrategySolver
from app.modules.farm_buildings.farm_service import FarmService
from app.modules.farm_buildings.shed import Shed
from app.services.stock_service import StockService


@pytest.mark.asyncio
async def test_field_crop_all_vs_any(fast_client):
    """Test harvest_mode 'all' vs 'any' behavior."""
    field_all = Field(fast_client, farm_id=1, position=1, harvest_mode="all")
    field_any = Field(fast_client, farm_id=1, position=1, harvest_mode="any")
    fast_client.rid = "test_rid"

    # Field with 1 ready crop and 1 growing crop
    partial_garden = {
        "datablock": [
            1,
            {
                "1": {"phase": 4, "remain": 0, "iswater": 1, "harvest": 17},
                "2": {"phase": 2, "remain": 500, "iswater": 1, "harvest": 17},
            },
        ]
    }

    await field_all.update(partial_garden)
    await field_any.update(partial_garden)

    # 'all' should NOT crop because tile 2 is phase 2
    assert await field_all.crop() is False

    # 'any' SHOULD crop because tile 1 is phase 4
    with respx.mock:
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(
                200, json={"datablock": [1, {"1": {"phase": 0}, "2": {"phase": 2}}]}
            )
        )
        assert await field_any.crop() is True


@pytest.mark.asyncio
async def test_field_plant_and_water(fast_client):
    """Test autoplant and watergarden API calls."""
    field = Field(fast_client, farm_id=1, position=1)
    fast_client.rid = "test_rid"

    # Empty field with 0 planted tiles
    await field.update({"datablock": [1, {}]})
    carrot = Product(pid=17, name="Karotte", price=0.34, category="v")

    with respx.mock:
        # Mock autoplant
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "datablock": [
                            1,
                            {"1": {"phase": 1, "remain": 900, "iswater": 0, "harvest": 17}},
                        ]
                    },
                ),
                # Mock watergarden
                httpx.Response(
                    200,
                    json={
                        "datablock": [
                            1,
                            {"1": {"phase": 1, "remain": 600, "iswater": 1, "harvest": 17}},
                        ]
                    },
                ),
            ]
        )

        planted = await field.plant(carrot)
        assert planted is True
        assert len(field.tiles) == 1
        assert field.tiles[0].is_watered is False

        watered = await field.water()
        assert watered is True
        assert field.tiles[0].is_watered is True


def test_plant_strategy_solver(fast_client):
    """Test strategy resolution for plantMin and plantQuest."""
    stock = StockService(fast_client)
    p1 = Product(pid=1, name="Getreide", price=0.50, category="v", amount=800)
    p2 = Product(pid=2, name="Mais", price=1.20, category="v", amount=150)
    p17 = Product(pid=17, name="Karotte", price=0.34, category="v", amount=300)
    stock.products = {1: p1, 2: p2, 17: p17}

    # plantMin should choose Mais (PID 2) because it has lowest stock (150 < 300 < 800)
    best_min = PlantStrategySolver.resolve_candidate("plantMin", stock, min_products=500)
    assert best_min is not None
    assert best_min.pid == 2

    # plantQuest should prioritize Karotte if Quest needs 400 Karotten
    # Deficit Karotte: (400 + 500) - 300 = 600
    quest_needs = {17: 400}
    best_quest = PlantStrategySolver.resolve_candidate(
        "plantQuest", stock, min_products=500, quest_requirements=quest_needs
    )
    assert best_quest is not None
    assert best_quest.pid == 17


@pytest.mark.asyncio
async def test_shed_crop_and_feed_optimizer(fast_client):
    """Test animal shed harvesting and feeding cost optimization."""
    shed = Shed(fast_client, farm_id=1, position=2, building_id=2, name="Hühnerstall")
    fast_client.rid = "test_rid"

    stock = StockService(fast_client)
    stock.config.buffer_crops = 0
    stock.credits_kt = 1000.0
    # Feed options: Getreide (PID 1, 0.50 kT, time 3600), Mais (PID 2, 1.20 kT, time 7200)
    stock.products = {
        1: Product(pid=1, name="Getreide", price=0.50, category="v", amount=500),
        2: Product(pid=2, name="Mais", price=1.20, category="v", amount=500),
        9: Product(pid=9, name="Ei", price=2.00, category="t", amount=0),
    }

    # Barn: 10 chickens, rest=7200s, remain=0s (needs feed)
    # Option 1 (Getreide): 7200 / 3600 = 2 units/chicken * 10 = 20 units * 0.50 = 10.00 kT
    # Option 2 (Mais):     7200 / 7200 = 1 unit/chicken * 10 = 10 units * 1.20 = 12.00 kT
    # Optimizer should pick Getreide (PID 1) at 10.00 kT!
    barn_init_data = {
        "datablock": [
            1,
            {
                "1": {
                    "2": {
                        "pid": 9,
                        "animals": {"amount": 10},
                        "remain": 0,
                        "rest": 7200,
                        "feed": {
                            "1": {"time": 3600},
                            "2": {"time": 7200},
                        },
                    }
                }
            },
        ]
    }

    await shed.update(barn_init_data)
    assert shed.barn is not None
    assert shed.barn.animals_count == 10

    with respx.mock:
        # Mock inner_feed call
        respx.get(url__startswith="https://s1.myfreefarm.de/ajax/farm.php").mock(
            return_value=httpx.Response(
                200,
                json={
                    "datablock": [
                        1,
                        {
                            "1": {
                                "2": {
                                    "pid": 9,
                                    "animals": {"amount": 10},
                                    "remain": 7200,
                                    "rest": 7200,
                                    "feed": {},
                                }
                            }
                        },
                    ]
                },
            )
        )

        fed = await shed.feed(stock)
        assert fed is True
        assert shed.barn.remain_seconds == 7200


@pytest.mark.asyncio
async def test_farm_service_orchestration(fast_client):
    """Test FarmService building discovery from getfarms."""
    service = FarmService(fast_client)
    fast_client.rid = "test_rid"

    getfarms_payload = {
        "updateblock": {
            "farms": {
                "config": {
                    "building2product": {"2": 9},  # Building 2 is a Shed
                },
                "farms": {
                    "1": {
                        "1": {"buildingid": "1", "name": "Hauptacker"},
                        "2": {"buildingid": "2", "name": "Hühnerstall"},
                    }
                },
            }
        }
    }

    await service.update_farms(getfarms_payload)
    assert len(service.fields) == 1
    assert len(service.sheds) == 1
    assert service.fields[0].position == 1
    assert service.fields[0].name == "Hauptacker"
    assert service.sheds[0].position == 2
    assert service.sheds[0].name == "Hühnerstall"


def test_plant_strategy_solver_with_fixed_pid(fast_client):
    """Test strategy solver immediately selects product when fixed_pid is provided."""
    stock = StockService(fast_client)
    p8 = Product(pid=8, name="Kornblumen", price=1.50, category="v", amount=1000)
    p113 = Product(pid=113, name="Chili", price=3.20, category="v", amount=895)
    p1 = Product(pid=1, name="Getreide", price=0.50, category="v", amount=50)
    stock.products = {1: p1, 8: p8, 113: p113}

    # Even though Getreide (PID 1) has lowest stock, fixed_pid 8 selects Kornblumen
    candidate_8 = PlantStrategySolver.resolve_candidate(
        "plantMin", stock, min_products=500, fixed_pid=8
    )
    assert candidate_8 is not None
    assert candidate_8.pid == 8
    assert candidate_8.name == "Kornblumen"

    # fixed_pid 113 selects Chili
    candidate_113 = PlantStrategySolver.resolve_candidate(
        "plantMin", stock, min_products=500, fixed_pid=113
    )
    assert candidate_113 is not None
    assert candidate_113.pid == 113
    assert candidate_113.name == "Chili"


@pytest.mark.asyncio
async def test_farm_service_loop_with_farm_crops(fast_client):
    """Test FarmService applies configured farm_crops per farm."""
    config = FarmStrategyConfig(farm_crops={1: 8, 3: 8, 4: 113})
    service = FarmService(fast_client, config=config)
    fast_client.rid = "test_rid"

    stock = StockService(fast_client)
    stock.products = {
        8: Product(pid=8, name="Kornblumen", price=1.50, category="v", amount=1000),
        113: Product(pid=113, name="Chili", price=3.20, category="v", amount=895),
    }

    field_f1 = Field(fast_client, farm_id=1, position=1)
    field_f1.serve = AsyncMock()
    field_f4 = Field(fast_client, farm_id=4, position=2)
    field_f4.serve = AsyncMock()

    service.fields = [field_f1, field_f4]
    service.sheds = []
    service.fuelstations = []
    service.update_farms = AsyncMock()

    await service.loop(stock)

    field_f1.serve.assert_awaited_once()
    candidate_f1 = field_f1.serve.call_args[0][0]
    assert candidate_f1.pid == 8

    field_f4.serve.assert_awaited_once()
    candidate_f4 = field_f4.serve.call_args[0][0]
    assert candidate_f4.pid == 113


@pytest.mark.asyncio
async def test_farm_specialized_categories_config():
    """Test FarmStrategyConfig category mapping per farm."""
    config = FarmStrategyConfig()
    assert config.get_farm_category(5) == "ex"
    assert config.get_farm_category(6) == "alpin"
    assert config.get_farm_category(8) == "water"
    assert config.get_farm_category(10) == "spice"
    # Standard farms fallback to default plant_category ('v')
    assert config.get_farm_category(1) == "v"
    assert config.get_farm_category(2) == "v"
    assert config.get_farm_category(3) == "v"
    assert config.get_farm_category(4) == "v"


@pytest.mark.asyncio
async def test_plant_strategy_solver_farm_categories(fast_client):
    """Test PlantStrategySolver respects farm categories."""
    stock = StockService(fast_client)
    stock.products = {
        8: Product(pid=8, name="Kornblumen", price=1.50, category="v", amount=100),
        351: Product(pid=351, name="Ananas", price=14.50, category="ex", amount=200),
        700: Product(pid=700, name="Spitzwegerich", price=46.00, category="alpin", amount=50),
        705: Product(pid=705, name="Enzian", price=9.50, category="alpin", amount=30),
        950: Product(pid=950, name="Reis", price=90.00, category="water", amount=10),
        1100: Product(pid=1100, name="Pfeffer", price=30.00, category="spice", amount=15),
    }

    # 1. Farm 6: Only 'alpin'
    cand_f6 = PlantStrategySolver.resolve_candidate("plantMin", stock, category="alpin", farm_id=6)
    assert cand_f6 is not None
    assert cand_f6.category == "alpin"
    assert cand_f6.pid == 705  # Lowest amount (30 < 50)

    # 2. Farm 8: Only 'water'
    cand_f8 = PlantStrategySolver.resolve_candidate("plantMin", stock, category="water", farm_id=8)
    assert cand_f8 is not None
    assert cand_f8.category == "water"
    assert cand_f8.pid == 950

    # 3. Farm 5: Only 'ex'
    cand_f5 = PlantStrategySolver.resolve_candidate("plantMin", stock, category="ex", farm_id=5)
    assert cand_f5 is not None
    assert cand_f5.category == "ex"
    assert cand_f5.pid == 351

    # 4. Farm 10: Only 'spice'
    cand_f10 = PlantStrategySolver.resolve_candidate(
        "plantMin", stock, category="spice", farm_id=10
    )
    assert cand_f10 is not None
    assert cand_f10.category == "spice"
    assert cand_f10.pid == 1100


@pytest.mark.asyncio
async def test_plant_strategy_solver_mismatched_fixed_pid(fast_client):
    """Test PlantStrategySolver rejects mismatched fixed_pid and falls back to farm category."""
    stock = StockService(fast_client)
    stock.products = {
        8: Product(pid=8, name="Kornblumen", price=1.50, category="v", amount=100),
        700: Product(pid=700, name="Spitzwegerich", price=46.00, category="alpin", amount=50),
    }

    # Setting PID 8 ('v') on Farm 6 ('alpin') must be rejected and fall back to 'alpin'
    cand = PlantStrategySolver.resolve_candidate(
        "plantMin", stock, category="alpin", fixed_pid=8, farm_id=6
    )
    assert cand is not None
    assert cand.category == "alpin"
    assert cand.pid == 700

    # Setting matching PID 700 on Farm 6 is accepted
    cand_valid = PlantStrategySolver.resolve_candidate(
        "plantMin", stock, category="alpin", fixed_pid=700, farm_id=6
    )
    assert cand_valid is not None
    assert cand_valid.pid == 700


@pytest.mark.asyncio
async def test_farm_service_loop_with_specialized_farms(fast_client):
    """Test FarmService.loop supplies category-compliant candidates to specialized farm fields."""
    config = FarmStrategyConfig()
    service = FarmService(fast_client, config=config)
    fast_client.rid = "test_rid"

    stock = StockService(fast_client)
    stock.products = {
        8: Product(pid=8, name="Kornblumen", price=1.50, category="v", amount=100),
        351: Product(pid=351, name="Ananas", price=14.50, category="ex", amount=200),
        700: Product(pid=700, name="Spitzwegerich", price=46.00, category="alpin", amount=50),
        950: Product(pid=950, name="Reis", price=90.00, category="water", amount=10),
        1100: Product(pid=1100, name="Pfeffer", price=30.00, category="spice", amount=15),
    }

    f1 = Field(fast_client, farm_id=1, position=1)
    f1.serve = AsyncMock()
    f5 = Field(fast_client, farm_id=5, position=1)
    f5.serve = AsyncMock()
    f6 = Field(fast_client, farm_id=6, position=1)
    f6.serve = AsyncMock()
    f8 = Field(fast_client, farm_id=8, position=1)
    f8.serve = AsyncMock()
    f10 = Field(fast_client, farm_id=10, position=1)
    f10.serve = AsyncMock()

    service.fields = [f1, f5, f6, f8, f10]
    service.sheds = []
    service.fuelstations = []
    service.update_farms = AsyncMock()

    await service.loop(stock)

    # Check candidates passed to serve()
    assert f1.serve.call_args[0][0].category == "v"
    assert f5.serve.call_args[0][0].category == "ex"
    assert f6.serve.call_args[0][0].category == "alpin"
    assert f8.serve.call_args[0][0].category == "water"
    assert f10.serve.call_args[0][0].category == "spice"


@pytest.mark.asyncio
async def test_plant_strategy_solver_farm_rack_priority(fast_client):
    """Test solver prioritizes crops with available seeds in the specific farm's local rack."""
    stock = StockService(fast_client)
    stock.products = {
        705: Product(pid=705, name="Malve", price=40.0, category="alpin", amount=5000),
        708: Product(pid=708, name="Melisse", price=50.0, category="alpin", amount=2000),
    }
    # Global stock has 5000 Malve and 2000 Melisse.
    # But on Farm 6, Malve has 0 seeds in local rack, while Melisse has 300!
    stock.farm_stocks = {
        6: {705: 0, 708: 300}
    }

    quest_reqs = {705: 10000, 708: 5000}
    # Even though Malve has higher deficit (10000 - 5000 = 5000 vs 5000 - 2000 = 3000),
    # Melisse must be chosen because Farm 6 has 0 Malve seeds in its rack!
    candidate = PlantStrategySolver.resolve_candidate(
        "plantQuest", stock, category="alpin", quest_requirements=quest_reqs, farm_id=6
    )
    assert candidate is not None
    assert candidate.pid == 708
    assert candidate.name == "Melisse"
