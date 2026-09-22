"""Unit tests for FormulaDealerService (Stadt 2 & Powerups)."""

import httpx
import pytest
import respx

from app.config import FormulaDealerConfig
from app.core.client import MFFGameClient
from app.modules.formula.service import FormulaDealerService, bin_amount
from app.services.stock_service import StockService


def test_bin_amount():
    """Verify formula purchase batching logic."""
    assert bin_amount(0) == 0
    assert bin_amount(-5) == 0
    assert bin_amount(1) == 5
    assert bin_amount(5) == 5
    assert bin_amount(6) == 10
    assert bin_amount(10) == 10
    assert bin_amount(14) == 25
    assert bin_amount(25) == 25
    assert bin_amount(26) == 50
    assert bin_amount(50) == 50
    assert bin_amount(51) == 75


@pytest.mark.asyncio
async def test_activate_powerups(fast_client: MFFGameClient):
    """Test activating available powerups in rack."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    config = FormulaDealerConfig(
        enabled=True,
        auto_activate_powerups=True,
        required_formulas=["Zucchiniauflauf", "Apfelkompott"],
    )
    service = FormulaDealerService(fast_client, stock_service=stock, config=config)

    powerups_data = {
        "rack": {
            "15": {"0": 15, "2": "Zucchiniauflauf", "rack": "2"},
            "99": {"0": 99, "2": "NichtGewünschtesRezept", "rack": "5"},
            "16": {"0": 16, "2": "Apfelkompott", "rack": "0"},
        }
    }

    with respx.mock:
        activate_route = respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/farm.php",
            params__contains={"mode": "activatepowerup", "id": "15"},
        ).mock(return_value=httpx.Response(200, json={"datablock": [1]}))

        activated = await service.activate_powerups(powerups_data)
        assert activated == 2
        assert activate_route.call_count == 2


@pytest.mark.asyncio
async def test_buy_missing_formulas(fast_client: MFFGameClient):
    """Test purchasing missing formulas at formula dealer in City 2."""
    fast_client.rid = "test_rid"
    stock = StockService(fast_client)
    config = FormulaDealerConfig(
        enabled=True,
        auto_buy_formulas=True,
        formula_min=20,
        required_formulas=["Zucchiniauflauf", "Apfelkompott"],
    )
    service = FormulaDealerService(fast_client, stock_service=stock, config=config)

    dealer_init_response = {
        "datablock": [
            1,
            # Offers from dealer
            [
                {"0": 15, "2": "Zucchiniauflauf"},
                {"0": 16, "2": "Apfelkompott"},
                {"0": 20, "2": "AnderesRezept"},
            ],
            # Player stock: 15 has only 7 (missing 13 -> bins to 25), 16 has 25 (ok)
            [
                {"fid": "15", "amount": "7"},
                {"fid": "16", "amount": "25"},
            ],
        ]
    }

    with respx.mock:
        respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/city.php",
            params__contains={"mode": "initformuladealer"},
        ).mock(return_value=httpx.Response(200, json=dealer_init_response))

        buy_route = respx.get(
            url__startswith="https://s1.myfreefarm.de/ajax/city.php",
            params__contains={"mode": "buyformula", "formula": "15", "amount": "25"},
        ).mock(return_value=httpx.Response(200, json={"datablock": [1]}))

        bought = await service.buy_missing_formulas()
        assert bought == {"Zucchiniauflauf": 25}
        assert buy_route.called


@pytest.mark.asyncio
async def test_formula_dealer_disabled(fast_client: MFFGameClient):
    """Test formula dealer does nothing when disabled."""
    fast_client.rid = "test_rid"
    service = FormulaDealerService(
        fast_client,
        config=FormulaDealerConfig(enabled=False),
    )
    res = await service.run_cycle()
    assert res == {"enabled": False}
