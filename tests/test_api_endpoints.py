import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.auth import create_access_token
from app.main import app
from app.models.product import Product
from app.services.stock_service import StockService
from app.worker.scheduler import worker_scheduler

client = TestClient(app)


@pytest.fixture
def auth_headers():
    token = create_access_token({"sub": "mff", "role": "admin"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def mock_stock(fast_client):
    stock = StockService(fast_client)
    stock.credits_kt = 1234.50
    stock.products = {
        8: Product(pid=8, name="Kornblumen", price=1.50, category="v", amount=500),
        17: Product(pid=17, name="Karotte", price=0.34, category="v", amount=1200),
        25: Product(pid=25, name="Kuhmilch", price=4.50, category="t", amount=50),
    }
    worker_scheduler.last_stock_service = stock
    return stock


def test_health_endpoint():
    """Test public health check endpoint."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_dashboard_html_contains_new_tabs_and_circuit_breaker():
    """Test that static dashboard HTML contains spicehouse, fuelstation, contracts and circuit-breaker."""
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    assert "circuitBreakerBanner" in response.text
    assert "tab-spicehouse" in response.text
    assert "tab-fuelstation" in response.text
    assert "tab-contracts" in response.text
    assert "content-spicehouse" in response.text
    assert "content-fuelstation" in response.text
    assert "content-contracts" in response.text


def test_plants_endpoints(auth_headers, mock_stock):
    """Test GET /plants with filtering and single plant lookup."""
    # All plants
    res_all = client.get("/api/v1/plants", headers=auth_headers)
    assert res_all.status_code == 200
    data_all = res_all.json()
    assert len(data_all) == 3

    # Filter by category 't'
    res_cat = client.get("/api/v1/plants?category=t", headers=auth_headers)
    assert res_cat.status_code == 200
    data_cat = res_cat.json()
    assert len(data_cat) == 1
    assert data_cat[0]["pid"] == 25

    # Filter by search
    res_search = client.get("/api/v1/plants?search=Korn", headers=auth_headers)
    assert res_search.status_code == 200
    data_search = res_search.json()
    assert len(data_search) == 1
    assert data_search[0]["name"] == "Kornblumen"

    # Single plant by PID
    res_single = client.get("/api/v1/plants/8", headers=auth_headers)
    assert res_single.status_code == 200
    assert res_single.json()["name"] == "Kornblumen"

    # Not found plant
    res_not_found = client.get("/api/v1/plants/9999", headers=auth_headers)
    assert res_not_found.status_code == 404


def test_farms_endpoints(auth_headers, mock_stock):
    """Test GET /farms and PUT /farms for farm crops configuration."""
    # GET /farms
    res = client.get("/api/v1/farms", headers=auth_headers)
    assert res.status_code == 200
    farms = res.json()
    assert isinstance(farms, list)
    assert len(farms) >= 4

    # PUT /farms
    new_config = {"1": 8, "3": 8, "4": 113}
    res_put = client.put("/api/v1/farms", json=new_config, headers=auth_headers)
    assert res_put.status_code == 200
    assert settings.agriculture.farm_crops.get(1) == 8
    assert settings.agriculture.farm_crops.get(4) == 113


def test_stock_endpoints(auth_headers, mock_stock):
    """Test GET /stock overview and stock buffer configuration."""
    res = client.get("/api/v1/stock", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["creditsKt"] == 1234.50
    assert data["productsCount"] == 3
    assert data["totalItems"] == 1750

    # Orders GET and PUT
    res_orders = client.get("/api/v1/stock/orders", headers=auth_headers)
    assert res_orders.status_code == 200

    res_put = client.put(
        "/api/v1/stock/orders",
        json={"bufferCrops": 600, "minCreditKt": 450.0},
        headers=auth_headers,
    )
    assert res_put.status_code == 200
    assert settings.stock.buffer_crops == 600
    assert settings.stock.min_credit_kt == 450.0


def test_offers_endpoints(auth_headers):
    """Test GET /offers and PUT /offers for marketplace trade configuration."""
    res = client.get("/api/v1/offers", headers=auth_headers)
    assert res.status_code == 200

    res_put = client.put(
        "/api/v1/offers",
        json={"enabled": True, "minCreditKt": 300.0, "autoSellSurplus": True},
        headers=auth_headers,
    )
    assert res_put.status_code == 200
    assert settings.trade.enabled is True
    assert settings.trade.min_credit_kt == 300.0


def test_contracts_endpoints(auth_headers, mock_stock):
    """Test GET /contracts and PUT /contracts."""
    contracts_payload = {
        "TestPartner": [
            {"name": "Kornblumen", "pid": 8, "amount": 100, "min": 50, "price": 1.20}
        ]
    }
    res_put = client.put("/api/v1/contracts", json=contracts_payload, headers=auth_headers)
    assert res_put.status_code == 200

    res_get = client.get("/api/v1/contracts", headers=auth_headers)
    assert res_get.status_code == 200
    data = res_get.json()
    assert "TestPartner" in data
    contract = data["TestPartner"][0]
    assert contract["inStock"] == 500
    assert contract["ready"] is True


def test_bot_trigger(auth_headers, monkeypatch):
    """Test POST /bot/trigger initiates a cycle without running live calls."""
    from unittest.mock import AsyncMock

    monkeypatch.setattr(worker_scheduler, "run_cycle", AsyncMock())
    res = client.post("/api/v1/bot/trigger", headers=auth_headers)
    assert res.status_code == 200
    assert res.json()["status"] in ("started", "skipped")


def test_fuelstation_endpoints(auth_headers, fast_client, mock_stock):
    """Test GET /fuelstation and PUT /fuelstation."""
    from app.models.fuelstation import FuelstationSlot
    from app.modules.farm_buildings.farm_service import FarmService
    from app.modules.farm_buildings.fuelstation import Fuelstation

    farm_svc = FarmService(fast_client)
    fs = Fuelstation(fast_client, farm_id=4, position=6)
    fs.slots[1] = FuelstationSlot(
        slot_id=1,
        level=5,
        production_limit=5_000_000,
        current_points=1_000_000,
        remain=0,
        busy=False,
        accepted_products={2: 1128, 18: 528},
    )
    farm_svc.fuelstations = [fs]
    worker_scheduler.last_farm_service = farm_svc

    # GET /fuelstation
    res = client.get("/api/v1/fuelstation", headers=auth_headers)
    assert res.status_code == 200
    data = res.json()
    assert "config" in data
    assert "fuelstations" in data
    assert len(data["fuelstations"]) == 1
    slot1 = data["fuelstations"][0]["slots"][0]
    assert slot1["slotId"] == 1
    assert slot1["productionLimit"] == 5_000_000
    assert slot1["currentPoints"] == 1_000_000
    assert slot1["pointsNeeded"] == 4_000_000
    assert slot1["isWaitingForRefill"] is True
    assert len(slot1["acceptedProducts"]) == 2

    # PUT /fuelstation
    put_payload = {
        "enabled": True,
        "autoHarvest": True,
        "autoRefill": True,
        "minReserve": 150,
        "preferredPids": [2, 18],
        "slotPreferredPids": {
            "1": [2, 18],
            "2": [18, 17],
        },
    }
    res_put = client.put("/api/v1/fuelstation", json=put_payload, headers=auth_headers)
    assert res_put.status_code == 200
    updated = res_put.json()["config"]
    assert updated["enabled"] is True
    assert updated["min_reserve"] == 150
    assert settings.fuelstation.min_reserve == 150
    assert settings.fuelstation.preferred_pids == [2, 18]
    assert settings.fuelstation.slot_preferred_pids == {1: [2, 18], 2: [18, 17]}


def test_bot_circuit_breaker_endpoints(auth_headers):
    """Test GET /bot/status includes circuit breaker and POST /bot/circuit-breaker/reset clears it."""
    from app.core.circuit_breaker import circuit_breaker

    circuit_breaker.trip("Test Block")
    try:
        res = client.get("/api/v1/bot/status", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert "circuit_breaker" in data
        assert data["circuit_breaker"]["is_open"] is True
        assert data["circuit_breaker"]["trip_reason"] == "Test Block"

        # Reset via API
        res_reset = client.post("/api/v1/bot/circuit-breaker/reset", headers=auth_headers)
        assert res_reset.status_code == 200
        assert res_reset.json()["circuit_breaker"]["is_open"] is False
        assert circuit_breaker.is_open is False
    finally:
        circuit_breaker.reset()


