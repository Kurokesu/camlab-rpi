# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Display management: output policy, cursor policy and panel backlight.

With both an HDMI monitor and a DSI touch panel wired, HDMI wins while
connected and the panel takes over when it unplugs. Switching runs through
wlr-randr (Cage exposes zwlr_output_manager_v1). A disabled output loses its
wl_output, so Qt always sees exactly one screen.

Cursor visibility follows input events, not device presence: a KVM emulates a
permanent mouse, so presence would pin an arrow over the picture forever.
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

# _SETTLE_MS debounces Qt's screen-event burst before enforcing. The same
# beat after enforcing lets Qt pick up new output topology.
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


def enforce_output_policy() -> bool:
    """One synchronous policy pass: HDMI on and DSI off when HDMI is
    connected, DSI on otherwise. Returns True when outputs were switched.

    Needs no Qt event loop, so startup can call it before QApplication exists
    and connect to the compositor with the right output already up.
    """
    if not has_dsi_connector():  # HDMI-only rig: nothing to switch between
        return False
    outputs = _wlr_outputs()
    if not outputs:
        return False
    # Require HDMI in both views before dropping the panel, so a race cannot
    # leave zero outputs enabled.
    hdmi = any(_is_hdmi(n) for n in connected_connectors()) and any(_is_hdmi(n) for n in outputs)
    targets = [n for n in outputs if _is_hdmi(n) == hdmi]
    if not targets:
        return False
    # One config: the compositor applies enables and disables together.
    args: list[str] = []
    for name in targets:
        if not outputs[name]:
            args += ["--output", name, "--on"]
    for name in outputs:
        if name not in targets and outputs[name]:
            args += ["--output", name, "--off"]
    if not args:
        return False
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
        return False
    except subprocess.CalledProcessError as exc:
        log.error("wlr-randr switch failed: %s", exc.stderr)
        return False
    return True


class DisplayManager(QtCore.QObject):
    """Keeps exactly one output class enabled: HDMI when connected, else DSI."""

    # Active QScreen after an enforcement pass. MainWindow dedupes, so firing
    # after a no-op pass is fine.
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
        """Connect hotplug signals and run the initial enforcement pass.
        Without a DSI connector there is nothing to switch between, so
        only report the boot screen."""
        if not has_dsi_connector():
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
        # Give Qt a beat to pick up the new wl_output topology.
        QtCore.QTimer.singleShot(_SETTLE_MS, self._emit_changed)

    def _emit_changed(self) -> None:
        screen = self._app.primaryScreen()
        if screen is not None:
            self.display_changed.emit(screen)


class CursorPolicy(QtCore.QObject):
    """Blank the cursor until a real mouse moves, re-blank it on touch."""

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
                # Names the real device even for touch-synthesized mouse
                # events, so fingers never summon the cursor.
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
        else:
            self._app.setOverrideCursor(QtGui.QCursor(QtCore.Qt.CursorShape.BlankCursor))


# At 0 the operator cannot find the slider to bring the picture back.
BACKLIGHT_FLOOR_PCT = 5


class Backlight:
    """First /sys/class/backlight device (the DSI panel), if any."""

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
        """Present and writable (needs video group), so the GUI never offers
        a dead slider."""
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
        """Set brightness, floored so the panel never blacks out. False when
        sysfs write fails (e.g. not in the video group)."""
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
