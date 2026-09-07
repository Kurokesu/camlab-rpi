# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Output layout, screen topology, cursor policy and panel backlight.

Connected heads decide layout, display setting matters only when both are
present. Switching via wlr-randr. Cursor follows input events, not
device presence (KVM would pin an arrow).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from .drm import has_dsi_display
from .qt import QtCore, QtGui, QtWidgets, Signal
from .settings import DisplayMode

log = logging.getLogger(__name__)

_WLR_TIMEOUT_S = 2.0

_CAMLABCTL = "/usr/local/bin/camlabctl"
# Two udev triggers with settle per touchscreen
_TOUCH_TIMEOUT_S = 5.0

# Debounce Qt screen-event burst before enforcing. Same beat after lets Qt pick up new topology.
_SETTLE_MS = 300

# GPU render budget, larger output drops camera frames
_MONITOR_MAX = (1920, 1080)
# Nominal 60 reports as 59.94 or 60.03
_MAX_REFRESH_HZ = 60.5

_MODE_RE = re.compile(r"^(\d+)x(\d+) px, ([\d.]+) Hz(.*)$")
_POS_RE = re.compile(r"^Position: (-?\d+),(-?\d+)$")


@dataclass(frozen=True)
class Mode:
    width: int
    height: int
    refresh: float
    preferred: bool = False
    current: bool = False

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    def arg(self) -> str:
        return f"{self.width}x{self.height}@{self.refresh:.3f}Hz"


@dataclass(frozen=True)
class Output:
    name: str
    enabled: bool
    modes: tuple[Mode, ...] = ()
    pos: tuple[int, int] = (0, 0)

    @property
    def current(self) -> Mode | None:
        return next((m for m in self.modes if m.current), None)


@dataclass(frozen=True)
class Target:
    name: str
    mode: Mode | None
    pos: tuple[int, int]


@dataclass(frozen=True)
class Layout:
    on: tuple[Target, ...] = ()
    off: tuple[str, ...] = ()
    touch: tuple[float, ...] | None = None


def _blocks(text: str) -> list[tuple[str, list[str]]]:
    blocks: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if line and not line[0].isspace():
            blocks.append((line.split(None, 1)[0], []))
        elif blocks:
            blocks[-1][1].append(line.strip())
    return blocks


def parse_outputs(text: str) -> dict[str, Output]:
    """Cage lists connected sinks only."""
    outputs: dict[str, Output] = {}
    for name, body in _blocks(text):
        enabled = False
        pos = (0, 0)
        modes: list[Mode] = []
        for entry in body:
            if entry.startswith("Enabled:"):
                enabled = entry.split(":", 1)[1].strip() == "yes"
                continue
            found = _POS_RE.match(entry)
            if found:
                pos = (int(found[1]), int(found[2]))
                continue
            found = _MODE_RE.match(entry)
            if found:
                flags = found[4]
                modes.append(
                    Mode(
                        int(found[1]),
                        int(found[2]),
                        float(found[3]),
                        "preferred" in flags,
                        "current" in flags,
                    )
                )
        outputs[name] = Output(name, enabled, tuple(modes), pos)
    return outputs


def pick_mode(modes: Iterable[Mode]) -> Mode | None:
    """Falls back to preferred so sinks with no fitting mode still light."""
    modes = tuple(modes)
    fits = [
        m
        for m in modes
        if m.width <= _MONITOR_MAX[0]
        and m.height <= _MONITOR_MAX[1]
        and m.refresh <= _MAX_REFRESH_HZ
    ]
    if fits:
        return max(fits, key=lambda m: (m.width * m.height, m.refresh))
    return next((m for m in modes if m.preferred), None)


def native_mode(output: Output) -> Mode | None:
    return output.current or next((m for m in output.modes if m.preferred), None)


def touch_matrix(rect: tuple[int, int, int, int], bounds: tuple[int, int]) -> tuple[float, ...]:
    """libinput calibration confining touch to rect (x, y, w, h) within bounds."""
    x, y, w, h = rect
    bw, bh = bounds
    return (w / bw, 0.0, x / bw, 0.0, h / bh, y / bh)


def classify(names: Iterable[str]) -> tuple[str | None, str | None, tuple[str, ...]]:
    """Lowest HDMI name is the monitor, spare heads switch off."""
    hdmi = sorted(n for n in names if n.startswith("HDMI-"))
    dsi = sorted(n for n in names if n.startswith("DSI-"))
    monitor = hdmi[0] if hdmi else None
    return monitor, (dsi[0] if dsi else None), tuple(hdmi[1:])


def plan_layout(mode: DisplayMode, outputs: Mapping[str, Output], dsi_display: bool) -> Layout:
    monitor, panel, spare = classify(outputs)
    # Cage lights DSI connector even with no panel wired
    phantom = ()
    if panel is not None and not dsi_display:
        phantom, panel = (panel,), None

    if monitor is None:
        if panel is None:
            return Layout()  # nothing to fall back to, phantom stays lit
        return Layout(on=(Target(panel, None, (0, 0)),))

    mon_mode = pick_mode(outputs[monitor].modes)
    if panel is None:
        return Layout(on=(Target(monitor, mon_mode, (0, 0)),), off=spare + phantom)

    panel_target = Target(panel, None, (0, 0))
    if mode is DisplayMode.BUILTIN:
        return Layout(on=(panel_target,), off=spare + (monitor,))

    if mode is DisplayMode.BOTH:
        pw, ph = native_mode(outputs[panel]).size
        mw, mh = mon_mode.size
        return Layout(
            on=(panel_target, Target(monitor, mon_mode, (pw, 0))),
            off=spare,
            touch=touch_matrix((0, 0, pw, ph), (pw + mw, max(ph, mh))),
        )

    return Layout(on=(Target(monitor, mon_mode, (0, 0)),), off=spare + (panel,))


def _target_args(target: Target) -> list[str]:
    args = ["--output", target.name, "--on", "--pos", f"{target.pos[0]},{target.pos[1]}"]
    if target.mode is not None:
        args += ["--mode", target.mode.arg()]
    return args


def _satisfied(output: Output | None, target: Target) -> bool:
    if output is None or not output.enabled or output.pos != target.pos:
        return False
    return target.mode is None or output.current == target.mode


def _wlr_randr(args: Iterable[str] = ()) -> str | None:
    try:
        proc = subprocess.run(
            ["wlr-randr", *args],
            capture_output=True,
            text=True,
            timeout=_WLR_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.debug("wlr-randr unavailable: %s", exc)
        return None
    if proc.returncode != 0:
        log.error("wlr-randr failed: %s", proc.stderr.strip())
        return None
    return proc.stdout


def apply_output_layout(mode: DisplayMode) -> None:
    """Re-running is safe, nothing is written when layout already matches."""
    report = _wlr_randr()
    if report is None:
        return
    outputs = parse_outputs(report)
    if not outputs:
        return
    layout = plan_layout(mode, outputs, has_dsi_display())

    args: list[str] = []
    for target in layout.on:
        if not _satisfied(outputs.get(target.name), target):
            args += _target_args(target)
    if args:
        log.info("display layout (%s): %s", mode, " ".join(args))
        if _wlr_randr(args) is None:
            return
    if has_dsi_display():
        _apply_touch(layout.touch)

    stale = [n for n in layout.off if n in outputs and outputs[n].enabled]
    if not stale:
        return
    if not layout.on or not _all_lit(layout.on):
        log.error("keeping %s enabled, target outputs did not light", ", ".join(stale))
        return
    log.info("display off: %s", " ".join(stale))
    _wlr_randr([a for n in stale for a in ("--output", n, "--off")])


def _apply_touch(matrix: tuple[float, ...] | None) -> None:
    """camlabctl owns the udev rule and skips when it already matches."""
    args = ["clear"] if matrix is None else [f"{v:.6f}" for v in matrix]
    try:
        subprocess.run(
            ["sudo", "-n", _CAMLABCTL, "touch", *args],
            capture_output=True,
            text=True,
            timeout=_TOUCH_TIMEOUT_S,
            check=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.error("touch calibration failed: %s", exc)
    except subprocess.CalledProcessError as exc:
        log.error("touch calibration failed: %s", exc.stderr.strip())


def _all_lit(targets: Iterable[Target]) -> bool:
    """Confirm targets lit before anything is switched off."""
    report = _wlr_randr()
    if report is None:
        return False
    outputs = parse_outputs(report)
    return all(n.name in outputs and outputs[n.name].enabled for n in targets)


@dataclass(frozen=True)
class Topology:
    """Screens as Qt reports them after a hotplug settle."""

    panel: QtCore.QRect | None
    monitor: QtCore.QRect | None
    bounds: QtCore.QRect

    @classmethod
    def from_screens(cls, screens) -> Topology:
        screens = tuple(screens)
        by_name = {s.name(): s for s in screens}
        monitor, panel, _spare = classify(by_name)
        return cls(
            panel=by_name[panel].geometry() if panel else None,
            monitor=by_name[monitor].geometry() if monitor else None,
            bounds=screens[0].virtualGeometry() if screens else QtCore.QRect(),
        )


class DisplayManager(QtCore.QObject):
    """Applies output layout at boot and after every hotplug settle."""

    # Topology after every enforcement pass, no-ops and empty screen lists included.
    topology_changed = Signal(object)

    def __init__(self, app: QtWidgets.QApplication, get_mode: Callable[[], DisplayMode]):
        super().__init__(app)
        self._app = app
        self._get_mode = get_mode

        self._settle = QtCore.QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.setInterval(_SETTLE_MS)
        self._settle.timeout.connect(self._enforce)

    def start(self) -> None:
        """Connect hotplug signals and run the first enforcement pass."""
        self._app.screenAdded.connect(lambda _s: self._settle.start())
        self._app.screenRemoved.connect(lambda _s: self._settle.start())
        self._settle.start()

    def _enforce(self) -> None:
        apply_output_layout(self._get_mode())
        QtCore.QTimer.singleShot(_SETTLE_MS, self._emit_changed)

    def _emit_changed(self) -> None:
        self.topology_changed.emit(Topology.from_screens(self._app.screens()))


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
