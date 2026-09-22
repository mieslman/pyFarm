import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from loguru import logger
from pydantic import BaseModel

from app.config import (
    EventsSettingsConfig,
    FactoryConfig,
    FarmersMarketConfig,
    FarmStrategyConfig,
    FoodworldConfig,
    ForestryConfig,
    FormulaDealerConfig,
    FuelstationConfig,
    HelpersConfig,
    InsecthotelSettingsConfig,
    QuestConfig,
    SeasonpassSettingsConfig,
    SpicehouseSettingsConfig,
    StallSettingsConfig,
    StockConfig,
    SushibarConfig,
    TradeConfig,
    VehiclesConfig,
    settings,
)

# Registry of structured configuration sections mapped to their Pydantic model
SECTION_MODELS: dict[str, type[BaseModel]] = {
    "agriculture": FarmStrategyConfig,
    "trade": TradeConfig,
    "stock": StockConfig,
    "forestry": ForestryConfig,
    "quest": QuestConfig,
    "helpers": HelpersConfig,
    "formula_dealer": FormulaDealerConfig,
    "fuelstation": FuelstationConfig,
    "farmersmarket": FarmersMarketConfig,
    "foodworld": FoodworldConfig,
    "sushibar": SushibarConfig,
    "factories": FactoryConfig,
    "spicehouse": SpicehouseSettingsConfig,
    "seasonpass": SeasonpassSettingsConfig,
    "events": EventsSettingsConfig,
    "insecthotel": InsecthotelSettingsConfig,
    "stall": StallSettingsConfig,
    "vehicles": VehiclesConfig,
}

# Top-level scalar settings to persist
SCALAR_FIELDS: list[str] = [
    "poll_interval_seconds",
    "anti_detection_min_delay_ms",
    "anti_detection_max_delay_ms",
]


class SettingsManager:
    """Manages persistent JSON configuration storage on disk with atomic writes

    and automatic synchronization with in-memory application settings.
    """

    def __init__(self, config_path: Optional[Path] = None):
        if config_path is not None:
            self._config_path = config_path
        else:
            env_path = os.environ.get("MFF_USER_CONFIG_PATH")
            if env_path:
                self._config_path = Path(env_path)
            else:
                # Default: <project_root>/data/user_config.json
                base_dir = Path(__file__).resolve().parent.parent.parent
                self._config_path = base_dir / "data" / "user_config.json"

        self._lock = threading.Lock()
        self._last_loaded_at: Optional[str] = None
        self._last_saved_at: Optional[str] = None
        self._contracts: dict[str, Any] = {}

    @property
    def config_path(self) -> Path:
        return self._config_path

    @config_path.setter
    def config_path(self, path: Path) -> None:
        with self._lock:
            self._config_path = path

    def load_settings(self) -> dict[str, Any]:
        """Load configuration from disk and apply to global `settings`.

        If the file does not exist, defaults are written to disk.
        """
        with self._lock:
            if not self._config_path.exists():
                logger.info(
                    f"SettingsManager: Keine Konfigurationsdatei unter {self._config_path} gefunden. "
                    f"Initialisiere Defaults..."
                )
                self._save_unlocked()
                return self._export_dict_unlocked()

            try:
                content = self._config_path.read_text(encoding="utf-8")
                data = json.loads(content)
            except Exception as e:
                logger.opt(exception=True).error(
                    f"SettingsManager: Fehler beim Lesen der Konfigurationsdatei {self._config_path}: {e}. "
                    f"Verwende aktuelle Einstellungen weiter."
                )
                return self._export_dict_unlocked()

            # Apply registered sections to settings
            loaded_sections = []
            for section, model_cls in SECTION_MODELS.items():
                if section in data and isinstance(data[section], dict):
                    try:
                        validated = model_cls.model_validate(data[section])
                        setattr(settings, section, validated)
                        loaded_sections.append(section)
                    except Exception as e:
                        logger.warning(
                            f"SettingsManager: Validierungsfehler in Sektion '{section}': {e}. "
                            f"Überspringe diesen Abschnitt."
                        )

            # Apply scalar fields
            for field in SCALAR_FIELDS:
                if field in data and data[field] is not None:
                    try:
                        target_type = type(getattr(settings, field))
                        setattr(settings, field, target_type(data[field]))
                    except (ValueError, TypeError) as e:
                        logger.warning(f"SettingsManager: Konnte Feld '{field}' nicht parsen: {e}")

            if "contracts" in data and isinstance(data["contracts"], dict):
                self._contracts = data["contracts"]

            now_iso = datetime.now(timezone.utc).isoformat()
            self._last_loaded_at = now_iso
            logger.info(
                f"SettingsManager: {len(loaded_sections)} Sektionen erfolgreich geladen aus {self._config_path}."
            )
            return self._export_dict_unlocked()

    def save_settings(self) -> dict[str, Any]:
        """Serialize current in-memory `settings` and atomically write to disk."""
        with self._lock:
            return self._save_unlocked()

    save = save_settings

    def get_contracts(self) -> dict[str, Any]:
        """Return persisted player contracts."""
        with self._lock:
            return getattr(self, "_contracts", {})

    def set_contracts(self, contracts: dict[str, Any]) -> None:
        """Update and persist player contracts."""
        with self._lock:
            self._contracts = contracts
            self._save_unlocked()

    def update_section(self, section: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Update a specific configuration section in memory and persist it."""
        with self._lock:
            if section in SECTION_MODELS:
                model_cls = SECTION_MODELS[section]
                current_instance = getattr(settings, section, None)
                current_dict = current_instance.model_dump() if current_instance else {}
                current_dict.update(payload)
                validated = model_cls.model_validate(current_dict)
                setattr(settings, section, validated)
            elif section in SCALAR_FIELDS:
                target_type = type(getattr(settings, section))
                setattr(settings, section, target_type(payload))
            else:
                raise ValueError(f"Unbekannte Konfigurationssektion: {section}")

            return self._save_unlocked()

    def get_status(self) -> dict[str, Any]:
        """Return persistence status and file metadata."""
        with self._lock:
            exists = self._config_path.exists()
            size = self._config_path.stat().st_size if exists else 0
            mtime = (
                datetime.fromtimestamp(self._config_path.stat().st_mtime, tz=timezone.utc).isoformat()
                if exists
                else None
            )

            return {
                "file_path": str(self._config_path),
                "file_exists": exists,
                "file_size_bytes": size,
                "file_mtime": mtime,
                "last_loaded_at": self._last_loaded_at,
                "last_saved_at": self._last_saved_at,
                "sections": list(SECTION_MODELS.keys()),
            }

    def get_export_data(self) -> dict[str, Any]:
        """Return exportable configuration data (excluding passwords/secrets)."""
        with self._lock:
            return self._export_dict_unlocked()

    def _export_dict_unlocked(self) -> dict[str, Any]:
        """Build dictionary of all exportable sections."""
        doc: dict[str, Any] = {
            "_comment": "MyFreeFarm Engine - User Configuration",
            "_version": "1.0",
        }
        for section in SECTION_MODELS:
            instance = getattr(settings, section, None)
            if instance is not None:
                doc[section] = instance.model_dump(mode="json")

        for field in SCALAR_FIELDS:
            val = getattr(settings, field, None)
            if val is not None:
                doc[field] = val

        if hasattr(self, "_contracts") and self._contracts:
            doc["contracts"] = self._contracts

        return doc

    def _save_unlocked(self) -> dict[str, Any]:
        """Perform atomic write to disk while lock is held."""
        # Ensure target directory exists
        self._config_path.parent.mkdir(parents=True, exist_ok=True)

        doc = self._export_dict_unlocked()
        now_iso = datetime.now(timezone.utc).isoformat()
        doc["_last_saved"] = now_iso

        tmp_file = self._config_path.with_name(f"{self._config_path.name}.tmp")
        try:
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())

            # Atomic replace
            os.replace(tmp_file, self._config_path)
            self._last_saved_at = now_iso
            logger.debug(f"SettingsManager: Konfiguration atomar gespeichert nach {self._config_path}")
        except Exception as e:
            logger.opt(exception=True).error(
                f"SettingsManager: Fehler beim Speichern nach {self._config_path}: {e}"
            )
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except Exception:
                    pass
            raise

        return doc


# Global singleton instance
settings_manager = SettingsManager()
