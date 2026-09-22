"""
Tests für die Konfigurations-Persistenz (SettingsManager + /api/v1/config Endpoints).
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.core.auth import create_access_token
from app.core.settings_manager import SettingsManager
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def preserve_real_config():
    """Ensure tests modifying settings_manager restore the real user_config.json."""
    from app.core.settings_manager import settings_manager

    real_path = settings_manager.config_path
    original_bytes = real_path.read_bytes() if real_path.exists() else None
    try:
        yield
    finally:
        if original_bytes is not None:
            real_path.write_bytes(original_bytes)
            settings_manager.load_settings()


@pytest.fixture
def auth_headers():
    token = create_access_token({"sub": "mff", "role": "admin"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def tmp_config(tmp_path: Path) -> Path:
    """Returns a temporary config file path (does not create the file)."""
    return tmp_path / "test_user_config.json"


@pytest.fixture
def sm(tmp_config: Path) -> SettingsManager:
    """Returns a fresh SettingsManager pointing at a temp file."""
    return SettingsManager(config_path=tmp_config)


# ── SettingsManager Unit Tests ─────────────────────────────────────────────────

class TestSettingsManagerUnit:
    def test_initial_save_creates_file(self, sm: SettingsManager, tmp_config: Path):
        """load_settings() should create the JSON file from defaults if missing."""
        assert not tmp_config.exists()
        sm.load_settings()
        assert tmp_config.exists()
        data = json.loads(tmp_config.read_text())
        assert "_version" in data
        assert "_comment" in data

    def test_save_and_load_roundtrip_scalar(self, sm: SettingsManager, tmp_config: Path):
        """Scalar fields should survive a save → load round-trip."""
        orig_poll = settings.poll_interval_seconds
        orig_min = settings.anti_detection_min_delay_ms
        try:
            settings.poll_interval_seconds = 999
            settings.anti_detection_min_delay_ms = 1234
            sm.save_settings()

            # Reset to defaults
            settings.poll_interval_seconds = 300
            settings.anti_detection_min_delay_ms = 800

            sm.load_settings()
            assert settings.poll_interval_seconds == 999
            assert settings.anti_detection_min_delay_ms == 1234
        finally:
            settings.poll_interval_seconds = orig_poll
            settings.anti_detection_min_delay_ms = orig_min

    def test_save_and_load_fuelstation_section(self, sm: SettingsManager, tmp_config: Path):
        """FuelstationConfig section survives a round-trip."""
        orig_enabled = settings.fuelstation.enabled
        orig_reserve = settings.fuelstation.min_reserve
        try:
            settings.fuelstation.enabled = True
            settings.fuelstation.min_reserve = 777
            sm.save_settings()

            settings.fuelstation.enabled = False
            settings.fuelstation.min_reserve = 0

            sm.load_settings()
            assert settings.fuelstation.enabled is True
            assert settings.fuelstation.min_reserve == 777
        finally:
            settings.fuelstation.enabled = orig_enabled
            settings.fuelstation.min_reserve = orig_reserve

    def test_save_and_load_stall_section(self, sm: SettingsManager, tmp_config: Path):
        """StallSettingsConfig section survives a round-trip (uses min_stock_reserve)."""
        orig = settings.stall.min_stock_reserve
        try:
            settings.stall.enabled = True
            settings.stall.min_stock_reserve = 500
            sm.save_settings()

            settings.stall.enabled = False
            settings.stall.min_stock_reserve = 0

            sm.load_settings()
            assert settings.stall.enabled is True
            assert settings.stall.min_stock_reserve == 500
        finally:
            settings.stall.min_stock_reserve = orig

    def test_atomic_write_uses_no_tmp_file(self, sm: SettingsManager, tmp_config: Path):
        """Atomic write should not leave .tmp files behind."""
        sm.save_settings()
        tmp_file = tmp_config.with_name(tmp_config.name + ".tmp")
        assert not tmp_file.exists()

    def test_get_set_contracts(self, sm: SettingsManager, tmp_config: Path):
        """Contracts should persist through set_contracts → load → get_contracts."""
        payload = {"Alice": [{"pid": 17, "amount": 100, "min": 50}]}
        sm.set_contracts(payload)

        sm2 = SettingsManager(config_path=tmp_config)
        sm2.load_settings()
        loaded = sm2.get_contracts()
        assert loaded == payload

    def test_update_section_persists(self, sm: SettingsManager, tmp_config: Path):
        """update_section() should write to disk immediately."""
        sm.update_section("fuelstation", {"enabled": True, "min_reserve": 77})
        data = json.loads(tmp_config.read_text())
        assert data["fuelstation"]["enabled"] is True
        assert data["fuelstation"]["min_reserve"] == 77

    def test_get_status_reports_file_info(self, sm: SettingsManager, tmp_config: Path):
        """get_status() should reflect disk reality."""
        sm.save_settings()
        status = sm.get_status()
        assert status["file_exists"] is True
        assert status["file_size_bytes"] > 0
        assert status["last_saved_at"] is not None

    def test_load_missing_file_creates_defaults(self, sm: SettingsManager, tmp_config: Path):
        """If file is missing, load_settings() creates file with defaults."""
        sm.load_settings()
        assert tmp_config.exists()

    def test_corrupted_json_falls_back_gracefully(self, sm: SettingsManager, tmp_config: Path):
        """Corrupted JSON file should not crash – returns current in-memory config."""
        tmp_config.parent.mkdir(parents=True, exist_ok=True)
        tmp_config.write_text("{invalid json!!}", encoding="utf-8")
        result = sm.load_settings()
        # Should not raise and must return a dict
        assert isinstance(result, dict)

    def test_unknown_section_raises(self, sm: SettingsManager):
        """update_section() with an unknown key should raise ValueError."""
        with pytest.raises(ValueError, match="Unbekannte Konfigurationssektion"):
            sm.update_section("nonexistent_section", {})

    def test_export_dict_contains_all_sections(self, sm: SettingsManager):
        """get_export_data() should include all registered sections."""
        from app.core.settings_manager import SECTION_MODELS
        export = sm.get_export_data()
        for section in SECTION_MODELS:
            assert section in export, f"Section '{section}' missing from export"

    def test_save_idempotent(self, sm: SettingsManager, tmp_config: Path):
        """Multiple saves should overwrite correctly without raising errors."""
        for _ in range(3):
            sm.save_settings()
        assert tmp_config.exists()
        data = json.loads(tmp_config.read_text())
        assert "_last_saved" in data


# ── /api/v1/config REST Endpoint Tests ────────────────────────────────────────

class TestConfigEndpoints:
    def test_get_config_returns_sections(self, auth_headers):
        """GET /api/v1/config should return sections and scalar fields at top level."""
        res = client.get("/api/v1/config", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        # Scalar fields
        assert "poll_interval_seconds" in data
        assert "anti_detection_min_delay_ms" in data
        # Section models
        assert "stall" in data
        assert "fuelstation" in data
        # Metadata
        assert "_persistence" in data

    def test_get_config_status(self, auth_headers):
        """GET /api/v1/config/status should return file metadata."""
        res = client.get("/api/v1/config/status", headers=auth_headers)
        assert res.status_code == 200
        st = res.json()
        assert "file_exists" in st
        assert "file_size_bytes" in st
        assert "sections" in st

    def test_put_config_scalar(self, auth_headers):
        """PUT /api/v1/config with scalar fields should update and return them."""
        orig = settings.poll_interval_seconds
        try:
            payload = {"poll_interval_seconds": 600, "anti_detection_min_delay_ms": 1500}
            res = client.put("/api/v1/config", headers=auth_headers, json=payload)
            assert res.status_code == 200
            data = res.json()
            assert "poll_interval_seconds" in data.get("updated_scalars", []) or \
                   data.get("config", {}).get("poll_interval_seconds") == 600
            assert settings.poll_interval_seconds == 600
            assert settings.anti_detection_min_delay_ms == 1500
        finally:
            settings.poll_interval_seconds = orig

    def test_put_config_section(self, auth_headers):
        """PUT /api/v1/config with a section dict should update the section."""
        payload = {"fuelstation": {"enabled": True, "min_reserve": 88}}
        res = client.put("/api/v1/config", headers=auth_headers, json=payload)
        assert res.status_code == 200
        data = res.json()
        assert "fuelstation" in data.get("updated_sections", [])
        assert settings.fuelstation.enabled is True
        assert settings.fuelstation.min_reserve == 88

    def test_put_config_unknown_key_ignored(self, auth_headers):
        """PUT /api/v1/config with a key starting with '_' should be silently skipped."""
        payload = {"_comment": "should be skipped", "poll_interval_seconds": 300}
        res = client.put("/api/v1/config", headers=auth_headers, json=payload)
        assert res.status_code == 200

    def test_post_config_reload(self, auth_headers, tmp_path: Path):
        """POST /api/v1/config/reload should re-read from disk (integration, uses real SM)."""
        # Just verify the endpoint responds correctly
        res = client.post("/api/v1/config/reload", headers=auth_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"

    def test_get_config_requires_auth(self):
        """GET /api/v1/config without token should return 401/403."""
        res = client.get("/api/v1/config")
        assert res.status_code in (401, 403)

    def test_put_config_requires_auth(self):
        """PUT /api/v1/config without token should return 401/403."""
        res = client.put("/api/v1/config", json={})
        assert res.status_code in (401, 403)


# ── Persistence Integration via PUT Module Endpoints ──────────────────────────

class TestPutEndpointsPersistence:
    def test_put_fuelstation_persists(self, auth_headers):
        """PUT /api/v1/fuelstation should update fuelstation settings in memory."""
        payload = {
            "enabled": True,
            "min_reserve": 99,
            "auto_harvest": True,
            "auto_refill": False,
        }
        res = client.put("/api/v1/fuelstation", headers=auth_headers, json=payload)
        assert res.status_code == 200
        assert settings.fuelstation.enabled is True
        assert settings.fuelstation.min_reserve == 99

    def test_put_stall_settings_persists(self, auth_headers):
        """PUT /api/v1/stall/settings should update stall config (uses min_stock_reserve)."""
        payload = {
            "enabled": True,
            "auto_clear_depleted": True,
            "min_stock_reserve": 500,
        }
        res = client.put("/api/v1/stall/settings", headers=auth_headers, json=payload)
        assert res.status_code == 200
        assert settings.stall.enabled is True
        assert settings.stall.min_stock_reserve == 500

    def test_put_contracts_updates_contracts(self, auth_headers):
        """PUT /api/v1/contracts should store and return contract data."""
        from app.core.settings_manager import settings_manager as global_sm
        payload = {"Bob": [{"pid": 25, "amount": 200, "min": 100}]}
        res = client.put("/api/v1/contracts", headers=auth_headers, json=payload)
        assert res.status_code == 200
        data = res.json()
        assert "Bob" in data.get("contracts", {})
        # Verify in-memory state
        loaded = global_sm.get_contracts()
        assert "Bob" in loaded
