# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Output layout table, wlr-randr parsing, mode choice and touch matrix."""

from __future__ import annotations

from camlab.display import (
    Mode,
    Output,
    Target,
    classify,
    native_mode,
    parse_outputs,
    pick_mode,
    plan_layout,
    touch_matrix,
)
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


def test_classify_hdmi_a2_alone_is_the_monitor():
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
