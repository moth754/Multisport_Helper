"""Settings kept in data/config.json (git-ignored, mode 0600). Race data is never stored."""

import json
import os
import threading
from pathlib import Path

from .analysis import DEFAULTS as THRESHOLDS

DEFAULTS = {
    "webscorer_api_id": "",
    "webscorer_token": "",
    "race_id": "",
    "refresh_seconds": 20,
    "ui_password_hash": "",
    **THRESHOLDS,
}
SECRETS = ("webscorer_token", "ui_password_hash")
EDITABLE = ("webscorer_api_id", "webscorer_token", "refresh_seconds", *THRESHOLDS)


class ConfigError(Exception):
    pass


class Config:
    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._values = dict(DEFAULTS)
        if self.path.exists():
            try:
                stored = json.loads(self.path.read_text())
            except (OSError, ValueError):
                stored = {}
            self._values.update({k: v for k, v in stored.items() if k in DEFAULTS})

    def get(self, key):
        with self._lock:
            return self._values.get(key, DEFAULTS.get(key))

    def all(self):
        with self._lock:
            return dict(self._values)

    def thresholds(self):
        with self._lock:
            return {k: self._values[k] for k in THRESHOLDS}

    def public(self):
        """Settings for the page: secrets are replaced by whether they are set."""
        values = self.all()
        out = {k: values[k] for k in EDITABLE if k not in SECRETS}
        out["webscorer_token_set"] = bool(values["webscorer_token"])
        out["password_set"] = bool(values["ui_password_hash"])
        out["race_id"] = values["race_id"]
        return out

    def update(self, changes):
        clean = {}
        for key, value in changes.items():
            if key not in DEFAULTS:
                continue
            if key == "refresh_seconds":
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise ConfigError("Refresh interval must be a whole number of seconds") from None
                if not 5 <= value <= 600:
                    raise ConfigError("Refresh interval must be between 5 and 600 seconds")
            elif key in THRESHOLDS:
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    raise ConfigError("Check thresholds must be numbers") from None
                if value <= 0:
                    raise ConfigError("Check thresholds must be above zero")
            elif key == "race_id":
                value = str(value or "").strip()
                if value and not value.isdigit():
                    raise ConfigError("The race ID is the number in the Webscorer results link (raceid=…)")
            else:
                value = str(value or "").strip()
            clean[key] = value
        with self._lock:
            self._values.update(clean)
            self._save()

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        with open(tmp, "w") as fh:
            os.chmod(tmp, 0o600)
            json.dump(self._values, fh, indent=1)
        os.replace(tmp, self.path)
