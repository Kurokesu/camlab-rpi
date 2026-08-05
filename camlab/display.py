# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Output policy, cursor policy and panel backlight.

HDMI wins while connected, else DSI. Switching via wlr-randr. Cursor follows
input events, not device presence (KVM would pin an arrow).
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from .drm import connected_connectors, has_dsi_connector
from .qt import QtCore, QtGui, QtWidgets, Signal

log = logging.getLogger(__name__)

_WLR_TIMEOUT_S = 2.0

# Debounce Qt screen-event burst before enforcing. Same beat after lets Qt pick up new topology.
_SETTLE_MS = 300
# Safety net for DRM changes Qt never reported.
_POLL_MS = 2000


def _is_hdmi(name: str) -> bool:
    return name.startswith("HDMI-")


def _wlr_outputs() -> dict[str, bool]:
    """Compositor outputs as {name: enabled}, empty when wlr-randr fails."""
    try:
        proc = subprocess.run(
            ["wlr-randr"], capture_output=True, text=True, timeout=_WLR_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("wlr-randr unavailable: %s", exc)
        return {}
    if proc.returncode != 0:
        log.debug("wlr-randr failed: %s", proc.stderr.strip())
        return {}
    outputs: dict[str, bool] = {}
    current = None
    for line in proc.stdout.splitlines():
        if line and not line[0].isspace():
            current = line.split(None, 1)[0]
            outputs[current] = False
        elif current is not None and line.strip().startswith("Enabled:"):
            outputs[current] = line.split(":", 1)[1].strip() == "yes"
    return outputs


def enforce_output_policy() -> None:
    """HDMI on and DSI off when HDMI connected, else DSI on. No Qt loop required."""
    if not has_dsi_connector():  # HDMI-only rig: nothing to switch between
        return
    outputs = _wlr_outputs()
    if not outputs:
        return
    # Require HDMI in both views before dropping panel. Avoids zero-output race.
    hdmi = any(_is_hdmi(n) for n in connected_connectors()) and any(_is_hdmi(n) for n in outputs)
    targets = [n for n in outputs if _is_hdmi(n) == hdmi]
    if not targets:
        return
    # One config so compositor applies enables and disables together.
    args: list[str] = []
    for name in targets:
        if not outputs[name]:
            args += ["--output", name, "--on"]
    for name in outputs:
        if name not in targets and outputs[name]:
            args += ["--output", name, "--off"]
    if not args:
        return
    log.info("display switch: %s", " ".join(args))
    try:
        subprocess.run(
            ["wlr-randr", *args],
            capture_output=True,
            text=True,
            timeout=_WLR_TIMEOUT_S,
            check=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.error("wlr-randr switch failed: %s", exc)
    except subprocess.CalledProcessError as exc:
        log.error("wlr-randr switch failed: %s", exc.stderr)


class DisplayManager(QtCore.QObject):
    """Keeps exactly one output class enabled: HDMI when connected, else DSI."""

    # Active QScreen after every enforcement pass, no-ops included.
    display_changed = Signal(object)

    def __init__(self, app: QtWidgets.QApplication):
        super().__init__(app)
        self._app = app
        self._drm_state: set[str] = set()

        self._settle = QtCore.QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(_SETTLE_MS)
        self._settle.timeout.connect(self._enforce)

        self._poll = QtCore.QTimer(self)
        self._poll.setInterval(_POLL_MS)
        self._poll.timeout.connect(self._poll_drm)

    def start(self) -> None:
        """Connect hotplug signals and run the first enforcement pass."""
        if not has_dsi_connector():  # nothing to switch between, just report
            QtCore.QTimer.singleShot(0, self._emit_changed)
            return
        self._app.screenAdded.connect(lambda _s: self._settle.start())
        self._app.screenRemoved.connect(lambda _s: self._settle.start())
        self._drm_state = connected_connectors()
        self._poll.start()
        self._settle.start()

    def _poll_drm(self) -> None:
        state = connected_connectors()
        if state != self._drm_state:
            self._drm_state = state
            self._settle.start()

    def _enforce(self) -> None:
        enforce_output_policy()
        QtCore.QTimer.singleShot(_SETTLE_MS, self._emit_changed)

    def _emit_changed(self) -> None:
        screen = self._app.primaryScreen()
        if screen is not None:
            self.display_changed.emit(screen)


class CursorPolicy(QtCore.QObject):
    """Blank the cursor until a real mouse moves, re-blank it on touch.

    The app-wide filter costs a Python call per event, so it retires itself
    after the first reveal when no touchscreen is attached (nothing would
    ever re-blank). Panels are wired at boot, so a touchscreen cannot appear
    later on a rig that retired the filter.
    """

    def __init__(self, app: QtWidgets.QApplication):
        super().__init__(app)
        self._app = app
        self._visible = True
        self._err_logged = False
        app.installEventFilter(self)
        self._set_visible(False)

    def eventFilter(self, obj, event) -> bool:
        # PyQt aborts on exceptions escaping Qt virtuals, so never throw here.
        try:
            t = event.type()
            if t == QtCore.QEvent.Type.TouchBegin:
                self._set_visible(False)
            elif t == QtCore.QEvent.Type.MouseMove:
                # Names real device even for touch-synthesized events. Fingers never summon cursor.
                dev = event.pointingDevice()
                if dev is not None:
                    touch = dev.type() == QtGui.QInputDevice.DeviceType.TouchScreen
                    self._set_visible(not touch)
        except Exception:  # cursor state is cosmetic, never fatal
            if not self._err_logged:
                self._err_logged = True
                log.exception("cursor policy filter failed (once)")
        return False

    def _set_visible(self, visible: bool) -> None:
        if visible == self._visible:
            return
        self._visible = visible
        if visible:
            self._app.restoreOverrideCursor()
            self._maybe_retire()
        else:
            self._app.setOverrideCursor(QtGui.QCursor(QtCore.Qt.CursorShape.BlankCursor))

    def _maybe_retire(self) -> None:
        touch = QtGui.QInputDevice.DeviceType.TouchScreen
        if any(d.type() == touch for d in QtGui.QInputDevice.devices()):
            return
        self._app.removeEventFilter(self)


# At 0 the operator cannot find the slider to bring the picture back.
BACKLIGHT_FLOOR_PCT = 5


class Backlight:
    """First /sys/class/backlight device (DSI panel), if any."""

    def __init__(self, root: Path = Path("/sys/class/backlight")):
        self._dir: Path | None = None
        self._max = 0
        devices = sorted(root.iterdir()) if root.is_dir() else []
        for dev in devices:
            try:
                self._max = int((dev / "max_brightness").read_text())
                self._dir = dev
                break
            except (OSError, ValueError):
                continue

    @property
    def available(self) -> bool:
        """Present and writable (needs video group). GUI never offers dead slider."""
        return (
            self._dir is not None and self._max > 0 and os.access(self._dir / "brightness", os.W_OK)
        )

    def get_percent(self) -> int | None:
        if not self.available:
            return None
        try:
            raw = int((self._dir / "brightness").read_text())
        except (OSError, ValueError):
            return None
        return round(raw * 100 / self._max)

    def set_percent(self, pct: int) -> bool:
        """Set brightness floored so panel never blacks out. False when write fails."""
        if not self.available:
            return False
        pct = min(max(int(pct), BACKLIGHT_FLOOR_PCT), 100)
        raw = max(1, round(pct * self._max / 100))
        try:
            (self._dir / "brightness").write_text(f"{raw}\n")
        except OSError as exc:
            log.warning("backlight write failed: %s", exc)
            return False
        return True
