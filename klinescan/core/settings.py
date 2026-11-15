"""Persistent application settings manager for KLineScan."""

import json
import logging
import os
from typing import Any, Dict

from klinescan.core.fileio import atomic_write_text

log = logging.getLogger(__name__)


class AppSettings:
    DEFAULT_SETTINGS: Dict[str, Any] = {
        "theme": "dark_workshop",
        "transport": "Fake ECU (Offline)",
        "port": "MOCK_PORT",
        "baud_mode": "Automatic (Profile)",
        "custom_baud": 10400,
        "echo_enabled": True,
        "poll_interval_ms": 250,
        "metric_units": True,
        "selected_pack": "generic_obd2.json",
        "selected_profile_index": 0,
        "selected_target_index": 0,
        "check_for_updates": True,
    }

    def __init__(self, config_path: str = ""):
        if not config_path:
            config_dir = os.path.join(os.path.expanduser("~"), ".klinescan")
            os.makedirs(config_dir, exist_ok=True)
            self._path = os.path.join(config_dir, "config.json")
        else:
            self._path = config_path
        self._data: Dict[str, Any] = dict(self.DEFAULT_SETTINGS)
        self.load()

    def load(self) -> None:
        if os.path.exists(self._path):
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self._data.update(loaded)
            except (OSError, ValueError) as exc:
                log.warning("settings %s unreadable, using defaults: %s", self._path, exc)

    def save(self) -> None:
        try:
            atomic_write_text(self._path, json.dumps(self._data, indent=2))
        except OSError as exc:
            log.error("could not save settings to %s: %s", self._path, exc)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default if default is not None else self.DEFAULT_SETTINGS.get(key))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value
        self.save()
