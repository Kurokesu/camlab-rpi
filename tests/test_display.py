# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Output layout, wlr-randr parsing, mode choice, touch matrix, topology and cursor policy."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from camlab import display
from camlab.display import (
    Mode,
    Output,
    Target,
    Topology,
    classify,
    native_mode,
    parse_outputs,
    pick_mode,
    plan_layout,
    touch_matrix,
)
from camlab.qt import QtCore, QtGui, Signal
from camlab.settings import DisplayMode

REPORT = """DSI-2 "(null) (null) (DSI-2)"
  Make: (null)
  Model: (null)
  Serial: (null)
  Physical size: 154x86 mm
  Enabled: yes
  Modes:
    800x480 px, 60.028999 Hz (preferred, current)
  Position: 0,0
  Transform: normal
  Scale: 1.000000
HDMI-A-1 "Generic 4K Monitor (HDMI-A-1)"
  Make: Generic
  Model: 4K Monitor
  Serial: 0000000
  Physical size: 700x390 mm
  Enabled: yes
  Modes:
    3840x2160 px, 59.996502 Hz
    2560x1440 px, 59.950001 Hz (preferred)
    1920x1080 px, 60.000000 Hz (current)
    1920x1080 px, 50.000000 Hz
    1280x720 px, 60.000000 Hz
  Position: 800,0
  Transform: normal
  Scale: 1.000000
"""

DISABLED_REPORT = """HDMI-A-1 "Generic 4K Monitor (HDMI-A-1)"
  Enabled: no
  Modes:
    1920x1080 px, 60.000000 Hz (preferred)
"""

PANEL = Output("DSI-2", True, (Mode(800, 480, 60.029, preferred=True, current=True),))
MONITOR_MODES = (
    Mode(3840, 2160, 59.9965),
    Mode(2560, 1440, 59.95, preferred=True),
    Mode(1920, 1080, 60.0, current=True),
    Mode(1920, 1080, 50.0),
    Mode(1280, 720, 60.0),
)
MONITOR = Output("HDMI-A-1", True, MONITOR_MODES, pos=(800, 0))
PICKED = Mode(1920, 1080, 60.0, current=True)

UHD = Mode(3840, 2160, 60.0, preferred=True)
UHD_MONITOR = Output("HDMI-A-1", True, (Mode(3840, 2160, 30.0), UHD))


def _outputs(*outs: Output) -> dict[str, Output]:
    return {o.name: o for o in outs}


def test_parse_report():
    outputs = parse_outputs(REPORT)
    assert set(outputs) == {"DSI-2", "HDMI-A-1"}
    panel = outputs["DSI-2"]
    assert panel.enabled is True
    assert panel.pos == (0, 0)
    assert panel.current == Mode(800, 480, 60.028999, preferred=True, current=True)
    monitor = outputs["HDMI-A-1"]
    assert len(monitor.modes) == 5
    assert monitor.pos == (800, 0)
    assert monitor.current.size == (1920, 1080)
    assert native_mode(monitor).size == (1920, 1080)


def test_parse_disabled_output():
    outputs = parse_outputs(DISABLED_REPORT)
    assert outputs["HDMI-A-1"].enabled is False
    assert outputs["HDMI-A-1"].current is None
    assert native_mode(outputs["HDMI-A-1"]).size == (1920, 1080)


def test_parse_empty_report():
    assert parse_outputs("") == {}


def test_mode_arg_uses_wlr_randr_syntax():
    assert Mode(1920, 1080, 60.0).arg() == "1920x1080@60.000Hz"


def test_pick_mode_caps_4k_monitor():
    assert pick_mode(MONITOR_MODES).size == (1920, 1080)


def test_pick_mode_prefers_faster_at_same_size():
    assert pick_mode((Mode(1920, 1080, 50.0), Mode(1920, 1080, 60.0))).refresh == 60.0


def test_pick_mode_skips_high_refresh():
    assert pick_mode((Mode(1920, 1080, 144.0), Mode(1280, 720, 60.0))).size == (1280, 720)


def test_pick_mode_accepts_nominal_60():
    assert pick_mode((Mode(1920, 1080, 60.028999),)).size == (1920, 1080)


def test_pick_mode_falls_back_to_preferred():
    modes = (Mode(3840, 2160, 30.0), Mode(3840, 2160, 60.0, preferred=True))
    assert pick_mode(modes).size == (3840, 2160)


def test_pick_mode_no_modes():
    assert pick_mode(()) is None


def test_classify_lowest_hdmi_wins():
    assert classify(["HDMI-A-2", "DSI-2", "HDMI-A-1"]) == ("HDMI-A-1", "DSI-2", ("HDMI-A-2",))


def test_classify_hdmi_a2_alone_is_monitor():
    assert classify(["HDMI-A-2"]) == ("HDMI-A-2", None, ())


def test_touch_matrix_matches_measured_rig():
    assert touch_matrix((0, 0, 800, 480), (2720, 1080)) == (
        800 / 2720,
        0.0,
        0.0,
        0.0,
        480 / 1080,
        0.0,
    )


def test_monitor_only_rig_ignores_mode():
    for mode in DisplayMode:
        layout = plan_layout(mode, _outputs(MONITOR), dsi_display=False)
        assert layout.on == (Target("HDMI-A-1", PICKED, (0, 0)),)
        assert layout.off == ()
        assert layout.touch is None


def test_spare_hdmi_switched_off():
    spare = Output("HDMI-A-2", True, MONITOR_MODES)
    layout = plan_layout(DisplayMode.EXTERNAL, _outputs(MONITOR, spare), dsi_display=False)
    assert layout.on == (Target("HDMI-A-1", PICKED, (0, 0)),)
    assert layout.off == ("HDMI-A-2",)


def test_phantom_dsi_switched_off():
    layout = plan_layout(DisplayMode.EXTERNAL, _outputs(PANEL, MONITOR), dsi_display=False)
    assert layout.on == (Target("HDMI-A-1", PICKED, (0, 0)),)
    assert layout.off == ("DSI-2",)


def test_phantom_dsi_alone_stays_lit():
    layout = plan_layout(DisplayMode.EXTERNAL, _outputs(PANEL), dsi_display=False)
    assert layout.on == ()
    assert layout.off == ()


def test_nothing_connected():
    layout = plan_layout(DisplayMode.EXTERNAL, {}, dsi_display=True)
    assert layout.on == ()
    assert layout.off == ()
    assert layout.touch is None


def test_panel_only_ignores_mode():
    for mode in DisplayMode:
        layout = plan_layout(mode, _outputs(PANEL), dsi_display=True)
        assert layout.on == (Target("DSI-2", None, (0, 0)),)
        assert layout.off == ()
        assert layout.touch is None


def test_external_enables_monitor_and_drops_panel():
    layout = plan_layout(DisplayMode.EXTERNAL, _outputs(PANEL, MONITOR), dsi_display=True)
    assert layout.on == (Target("HDMI-A-1", PICKED, (0, 0)),)
    assert layout.off == ("DSI-2",)
    assert layout.touch is None


def test_builtin_enables_panel_and_drops_monitor():
    layout = plan_layout(DisplayMode.BUILTIN, _outputs(PANEL, MONITOR), dsi_display=True)
    assert layout.on == (Target("DSI-2", None, (0, 0)),)
    assert layout.off == ("HDMI-A-1",)
    assert layout.touch is None


def test_both_places_monitor_right_of_panel():
    layout = plan_layout(DisplayMode.BOTH, _outputs(PANEL, MONITOR), dsi_display=True)
    assert layout.on == (
        Target("DSI-2", None, (0, 0)),
        Target("HDMI-A-1", PICKED, (800, 0)),
    )
    assert layout.off == ()
    assert layout.touch == touch_matrix((0, 0, 800, 480), (2720, 1080))


def test_monitor_past_budget_still_lights_and_says_so(caplog):
    with caplog.at_level(logging.WARNING, logger="camlab.display"):
        layout = plan_layout(DisplayMode.EXTERNAL, _outputs(UHD_MONITOR), dsi_display=False)
    assert layout.on == (Target("HDMI-A-1", UHD, (0, 0)),)
    assert "HDMI-A-1" in caplog.text
    assert UHD.arg() in caplog.text


def test_monitor_within_budget_is_quiet(caplog):
    with caplog.at_level(logging.WARNING, logger="camlab.display"):
        plan_layout(DisplayMode.EXTERNAL, _outputs(MONITOR), dsi_display=False)
    assert caplog.records == []


def test_builtin_is_quiet_about_monitor_it_switches_off(caplog):
    with caplog.at_level(logging.WARNING, logger="camlab.display"):
        layout = plan_layout(DisplayMode.BUILTIN, _outputs(PANEL, UHD_MONITOR), dsi_display=True)
    assert layout.off == ("HDMI-A-1",)
    assert caplog.records == []


@pytest.fixture
def rig(monkeypatch):
    """REPORT as the live compositor, every wlr-randr write and shim call recorded."""
    calls: list[list[str]] = []

    def wlr(args=()):
        if args:
            calls.append(["wlr-randr", *args])
        return REPORT

    monkeypatch.setattr(display, "_wlr_randr", wlr)
    monkeypatch.setattr(display, "has_dsi_display", lambda: True)
    monkeypatch.setattr(display.subprocess, "run", lambda cmd, **kw: calls.append(list(cmd)))
    return calls


TOUCH_SET = ["sudo", "-n", display._CAMLABCTL, "touch"] + [
    f"{v:.6f}" for v in touch_matrix((0, 0, 800, 480), (2720, 1080))
]
TOUCH_CLEAR = ["sudo", "-n", display._CAMLABCTL, "touch", "clear"]


def test_apply_both_is_touch_only_when_outputs_already_match(rig):
    display.apply_output_layout(DisplayMode.BOTH)
    assert rig == [TOUCH_SET]


def test_apply_external_clears_touch_before_dropping_panel(rig):
    display.apply_output_layout(DisplayMode.EXTERNAL)
    assert rig == [
        ["wlr-randr", "--output", "HDMI-A-1", "--on", "--pos", "0,0", "--mode", PICKED.arg()],
        TOUCH_CLEAR,
        ["wlr-randr", "--output", "DSI-2", "--off"],
    ]


def test_apply_builtin_drops_monitor(rig):
    display.apply_output_layout(DisplayMode.BUILTIN)
    assert rig == [TOUCH_CLEAR, ["wlr-randr", "--output", "HDMI-A-1", "--off"]]


def test_apply_skips_touch_without_dsi_display(rig, monkeypatch):
    monkeypatch.setattr(display, "has_dsi_display", lambda: False)
    display.apply_output_layout(DisplayMode.BOTH)
    assert rig == [
        ["wlr-randr", "--output", "HDMI-A-1", "--on", "--pos", "0,0", "--mode", PICKED.arg()],
        ["wlr-randr", "--output", "DSI-2", "--off"],
    ]


def test_apply_never_disables_when_target_did_not_light(rig, monkeypatch):
    # Monitor reads disabled before and after the enable, panel must stay lit
    dark_monitor = "Enabled: no".join(REPORT.rsplit("Enabled: yes", 1))
    monkeypatch.setattr(display, "_wlr_randr", lambda args=(): dark_monitor)
    display.apply_output_layout(DisplayMode.EXTERNAL)
    assert rig == [TOUCH_CLEAR]


PANEL_RECT = QtCore.QRect(0, 0, 800, 480)
MONITOR_RECT = QtCore.QRect(800, 0, 1920, 1080)
UNION_RECT = QtCore.QRect(0, 0, 2720, 1080)


def _screen(name: str, geometry: QtCore.QRect, virtual: QtCore.QRect) -> SimpleNamespace:
    """QScreen stand-in, only the three methods Topology reads."""
    return SimpleNamespace(
        name=lambda: name, geometry=lambda: geometry, virtualGeometry=lambda: virtual
    )


def test_topology_panel_only():
    topo = Topology.from_screens([_screen("DSI-2", PANEL_RECT, PANEL_RECT)])
    assert topo == Topology(panel=PANEL_RECT, monitor=None, bounds=PANEL_RECT)


def test_topology_monitor_only():
    monitor = QtCore.QRect(0, 0, 1920, 1080)
    topo = Topology.from_screens([_screen("HDMI-A-1", monitor, monitor)])
    assert topo == Topology(panel=None, monitor=monitor, bounds=monitor)


def test_topology_panel_and_monitor():
    topo = Topology.from_screens(
        [_screen("DSI-2", PANEL_RECT, UNION_RECT), _screen("HDMI-A-1", MONITOR_RECT, UNION_RECT)]
    )
    assert topo == Topology(panel=PANEL_RECT, monitor=MONITOR_RECT, bounds=UNION_RECT)


def test_topology_ignores_spare_hdmi():
    spare = QtCore.QRect(2720, 0, 1920, 1080)
    union = QtCore.QRect(0, 0, 4640, 1080)
    topo = Topology.from_screens(
        [
            _screen("HDMI-A-2", spare, union),
            _screen("DSI-2", PANEL_RECT, union),
            _screen("HDMI-A-1", MONITOR_RECT, union),
        ]
    )
    assert topo == Topology(panel=PANEL_RECT, monitor=MONITOR_RECT, bounds=union)


def test_topology_no_screens():
    topo = Topology.from_screens([])
    assert topo.panel is None
    assert topo.monitor is None
    assert topo.bounds == QtCore.QRect()
    assert topo.bounds.isNull()


class FakeApp(QtCore.QObject):
    """QObject parent for DisplayManager with a scripted screen list."""

    def __init__(self, screens):
        super().__init__()
        self._screens = screens

    def screens(self):
        return self._screens


def test_manager_emits_topology_with_no_screens():
    manager = display.DisplayManager(FakeApp([]), lambda: DisplayMode.EXTERNAL)
    seen: list[Topology] = []
    manager.topology_changed.connect(seen.append)
    manager._emit_changed()
    assert seen == [Topology(panel=None, monitor=None, bounds=QtCore.QRect())]


def test_manager_emits_topology_from_app_screens():
    screens = [
        _screen("DSI-2", PANEL_RECT, UNION_RECT),
        _screen("HDMI-A-1", MONITOR_RECT, UNION_RECT),
    ]
    manager = display.DisplayManager(FakeApp(screens), lambda: DisplayMode.BOTH)
    seen: list[Topology] = []
    manager.topology_changed.connect(seen.append)
    manager._emit_changed()
    assert seen == [Topology(panel=PANEL_RECT, monitor=MONITOR_RECT, bounds=UNION_RECT)]


class ScreenStub(QtCore.QObject):
    """QScreen stand-in with the one signal DisplayManager watches."""

    geometryChanged = Signal(object)


class HotplugApp(QtCore.QObject):
    """QApplication stand-in: screen signals over a mutable screen list."""

    screenAdded = Signal(object)
    screenRemoved = Signal(object)

    def __init__(self, *screens: ScreenStub):
        super().__init__()
        self.lit = list(screens)

    def screens(self) -> list[ScreenStub]:
        return list(self.lit)

    def plug(self, screen: ScreenStub) -> None:
        self.lit.append(screen)
        self.screenAdded.emit(screen)


class SettleSpy:
    """Debounce timer stand-in, counting arms instead of firing."""

    def __init__(self):
        self.arms = 0

    def start(self) -> None:
        self.arms += 1


def _spied(app: HotplugApp) -> tuple[display.DisplayManager, SettleSpy]:
    """Manager with the debounce swapped for a spy."""
    manager = display.DisplayManager(app, lambda: DisplayMode.EXTERNAL)
    spy = SettleSpy()
    manager._settle = spy
    return manager, spy


NEW_GEOMETRY = QtCore.QRect(0, 0, 1280, 720)


def test_mode_change_on_live_output_runs_pass():
    screen = ScreenStub()
    manager, spy = _spied(HotplugApp(screen))
    manager.start()
    assert spy.arms == 1
    screen.geometryChanged.emit(NEW_GEOMETRY)
    assert spy.arms == 2


def test_mode_change_on_hotplugged_output_runs_pass():
    app = HotplugApp()
    manager, spy = _spied(app)
    manager.start()
    screen = ScreenStub()
    app.plug(screen)
    screen.geometryChanged.emit(NEW_GEOMETRY)
    assert spy.arms == 3


def test_every_screen_signal_arms_one_debounce():
    screen = ScreenStub()
    app = HotplugApp(screen)
    manager, spy = _spied(app)
    manager.start()
    app.screenRemoved.emit(screen)
    screen.geometryChanged.emit(NEW_GEOMETRY)
    screen.geometryChanged.emit(NEW_GEOMETRY)
    assert spy.arms == 4


class CursorApp(QtCore.QObject):
    """QApplication stand-in recording cursor pushes and pops in order."""

    def __init__(self):
        super().__init__()
        self.filters: list[QtCore.QObject] = []
        self.calls: list[str] = []

    def installEventFilter(self, obj) -> None:
        self.filters.append(obj)

    def removeEventFilter(self, obj) -> None:
        self.filters.remove(obj)

    def setOverrideCursor(self, _cursor) -> None:
        self.calls.append("blank")

    def restoreOverrideCursor(self) -> None:
        self.calls.append("reveal")


class FakePointer:
    """Stand-in for QPointingDevice. Qt reuses one per seat, so replugs share it."""

    def __init__(self, kind=QtGui.QInputDevice.DeviceType.Mouse):
        self._kind = kind

    def type(self):
        return self._kind


class FakeMove:
    def __init__(self, device):
        self._device = device

    def type(self):
        return QtCore.QEvent.Type.MouseMove

    def pointingDevice(self):
        return self._device


@pytest.fixture
def cursor_rig(qapp, tmp_path):
    """Freshly built CursorPolicy over a stand-in app, blanked as at startup."""

    def build() -> tuple[CursorApp, display.CursorPolicy]:
        app = CursorApp()
        policy = display.CursorPolicy(app, input_dir=str(tmp_path))
        app.calls.clear()
        return app, policy

    return build


def test_cursor_filter_stays_installed(cursor_rig):
    app, policy = cursor_rig()
    policy.eventFilter(None, FakeMove(FakePointer()))
    assert app.filters == [policy]


def test_first_mouse_move_reveals_cursor(cursor_rig):
    app, policy = cursor_rig()
    policy.eventFilter(None, FakeMove(FakePointer()))
    assert app.calls == ["reveal"]


def test_further_moves_do_nothing(cursor_rig):
    app, policy = cursor_rig()
    mouse = FakePointer()
    for _ in range(5):
        policy.eventFilter(None, FakeMove(mouse))
    assert app.calls == ["reveal"]


def test_replug_gets_cursor_reapplied(cursor_rig):
    app, policy = cursor_rig()
    mouse = FakePointer()
    policy.eventFilter(None, FakeMove(mouse))
    app.calls.clear()
    # Qt hands back the same pointer after a replug, so the input dir is the cue
    policy._on_input_changed("/dev/input")
    policy.eventFilter(None, FakeMove(mouse))
    assert app.calls == ["blank", "reveal"]


def test_replug_rearms_only_once(cursor_rig):
    app, policy = cursor_rig()
    mouse = FakePointer()
    policy.eventFilter(None, FakeMove(mouse))
    policy._on_input_changed("/dev/input")
    for _ in range(4):
        policy.eventFilter(None, FakeMove(mouse))
    assert app.calls == ["reveal", "blank", "reveal"]


def test_touch_never_summons_cursor(cursor_rig):
    app, policy = cursor_rig()
    finger = FakePointer(QtGui.QInputDevice.DeviceType.TouchScreen)
    policy._on_input_changed("/dev/input")
    policy.eventFilter(None, FakeMove(finger))
    assert app.calls == []
