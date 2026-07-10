"""Boot-persistent settings store (per-sensor mode/fps and control overrides).

A tiny atomic JSON file. Selections are keyed by the sensor's dtoverlay token so
each sensor remembers its own last mode. Writes are unprivileged (no sudo): the
file lives on a dedicated writable data directory that survives an eventual
read-only root (Phase 5 mounts a data partition at /var/lib/camlab, excluded
from the overlay).

Path resolution, first hit wins:
  1. $CAMLAB_STATE_FILE         (explicit override, e.g. for tests)
  2. $STATE_DIRECTORY/state.json (set by systemd StateDirectory=camlab)
  3. /var/lib/camlab/state.json (fallback for manual runs)

Reads never raise: a missing or corrupt file is treated as "no saved selection".
Writes never raise: a read-only or full filesystem logs a warning and is ignored,
so the GUI keeps working (the selection is simply not remembered).
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

_VERSION = 1


def default_state_file() -> Path:
    override = os.environ.get("CAMLAB_STATE_FILE")
    if override:
        return Path(override)
    state_dir = os.environ.get("STATE_DIRECTORY")
    if state_dir:
        # systemd may pass a colon-separated list. The first entry is ours.
        return Path(state_dir.split(":")[0]) / "state.json"
    return Path("/var/lib/camlab/state.json")


class SettingsStore:
    def __init__(self, path: str | os.PathLike | None = None):
        self._path = Path(path) if path else default_state_file()

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> dict:
        try:
            with open(self._path, "r") as f:
                data = json.load(f)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.warning("settings unreadable (%s): %s - ignoring", self._path, exc)
            return {}
        if not isinstance(data, dict) or data.get("version") != _VERSION:
            log.warning("settings schema mismatch in %s - ignoring", self._path)
            return {}
        return data

    def get_mode(self, overlay: str) -> dict | None:
        """Return {'size': [w, h], 'bit_depth': int, 'fps': float} or None."""
        if not overlay:
            return None
        entry = (self._load().get("modes") or {}).get(overlay)
        if not isinstance(entry, dict):
            return None
        try:
            size = entry["size"]
            return {
                "size": [int(size[0]), int(size[1])],
                "bit_depth": int(entry["bit_depth"]),
                "fps": float(entry["fps"]),
            }
        except (KeyError, TypeError, ValueError, IndexError):
            log.warning("settings entry for %s is malformed - ignoring", overlay)
            return None

    def set_mode(self, overlay: str, size: tuple[int, int], bit_depth: int,
                 fps: float) -> bool:
        """Persist a selection for a sensor. Returns True if written."""
        if not overlay:
            return False
        data = self._load()
        data["version"] = _VERSION
        data.setdefault("modes", {})
        data["modes"][overlay] = {
            "size": [int(size[0]), int(size[1])],
            "bit_depth": int(bit_depth),
            "fps": float(fps),
        }
        return self._atomic_write(data)

    def get_controls(self, overlay: str) -> dict:
        """Per-sensor manual control overrides, None per control means auto.

        Always returns all three keys, so it can seed ControlState directly.
        """
        out = {"exposure_us": None, "gain": None, "colour_temp": None}
        if not overlay:
            return out
        entry = (self._load().get("controls") or {}).get(overlay)
        if not isinstance(entry, dict):
            return out
        try:
            if entry.get("exposure_us") is not None:
                out["exposure_us"] = int(entry["exposure_us"])
            if entry.get("gain") is not None:
                out["gain"] = float(entry["gain"])
            if entry.get("colour_temp") is not None:
                out["colour_temp"] = int(entry["colour_temp"])
        except (TypeError, ValueError):
            log.warning("controls entry for %s is malformed - ignoring", overlay)
            return {"exposure_us": None, "gain": None, "colour_temp": None}
        return out

    def set_controls(self, overlay: str, exposure_us: int | None,
                     gain: float | None, colour_temp: int | None) -> bool:
        """Persist control overrides for a sensor. Returns True if written."""
        if not overlay:
            return False
        data = self._load()
        data["version"] = _VERSION
        data.setdefault("controls", {})
        data["controls"][overlay] = {
            "exposure_us": int(exposure_us) if exposure_us is not None else None,
            "gain": float(gain) if gain is not None else None,
            "colour_temp": int(colour_temp) if colour_temp is not None else None,
        }
        return self._atomic_write(data)

    def get_histogram(self) -> bool:
        """App-level histogram overlay toggle, default off."""
        return bool((self._load().get("ui") or {}).get("histogram", False))

    def set_histogram(self, enabled: bool) -> bool:
        data = self._load()
        data["version"] = _VERSION
        data.setdefault("ui", {})["histogram"] = bool(enabled)
        return self._atomic_write(data)

    def _atomic_write(self, data: dict) -> bool:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self._path.parent),
                                       prefix=".state-", suffix=".json")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self._path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            return True
        except OSError as exc:
            log.warning("could not persist settings to %s: %s", self._path, exc)
            return False
