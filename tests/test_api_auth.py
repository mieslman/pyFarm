from fastapi.testclient import TestClient

from app.config import settings
from app.core.auth import create_access_token
from app.main import app

client = TestClient(app)


def test_login_success():
    """Test successful login with valid credentials."""
    response = client.post(
        "/api/v1/login",
        json={"username": settings.api_username, "password": settings.api_password},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "accessToken" in data
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 86400


def test_login_invalid_credentials():
    """Test login failure with invalid password."""
    response = client.post(
        "/api/v1/login",
        json={"username": "mff", "password": "wrong_password_123"},
    )
    assert response.status_code == 401
    assert "detail" in response.json()


def test_protected_endpoint_without_token():
    """Test access to protected endpoint without auth token returns 401."""
    response = client.get("/api/v1/bot/status")
    assert response.status_code == 401


def test_protected_endpoint_with_invalid_token():
    """Test access to protected endpoint with invalid token returns 401."""
    response = client.get(
        "/api/v1/bot/status",
        headers={"Authorization": "Bearer invalid.jwt.token"},
    )
    assert response.status_code == 401


def test_protected_endpoint_with_valid_token():
    """Test access to protected endpoint with valid JWT token."""
    token = create_access_token({"sub": "mff", "role": "admin"})
    response = client.get(
        "/api/v1/bot/status",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "is_running" in data
    assert "state" in data
