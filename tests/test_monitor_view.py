# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""MonitorView against a stub engine, fake mirror and scripted monitor sheet state."""

from __future__ import annotations

import os
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PyQt6")

from camlab.gui import focus_map
from camlab.gui.chips import CTRL_SPEC, chip_text
from camlab.gui.focus_map import FocusMapOverlay
from camlab.gui.histogram import HistogramOverlay
from camlab.gui.monitor_view import MonitorView
from camlab.qt import Qt, QtCore, QtWidgets, Signal
from camlab.settings import MonitorState

OFF = MonitorState(
    histogram=False, focus_map=False, peaking=False, zebra=False, zebra_threshold=0.95
)


def telemetry(frame=None, fps=0.0, **metadata) -> SimpleNamespace:
    return SimpleNamespace(frame=frame, fps=fps, metadata=metadata)


class FakeLive(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.assists = None

    def set_assists(self, peaking: bool, zebra: bool, threshold: float) -> None:
        self.assists = (peaking, zebra, threshold)


class FakeEngine:
    """Offers exposure and gain only, WB chip stays hidden."""

    def __init__(self):
        self.picam2 = object()
        self.telemetry = telemetry()
        self.control_state = SimpleNamespace(exposure_us=None, gain=None, colour_temp=None)
        self.current_mode = SimpleNamespace(
            size=(1920, 1080), label=lambda: "1920x1080 SRGGB12 30fps"
        )
        self.latest_histogram = None
        self.mirrors: list[FakeLive] = []

    def make_mirror(self) -> FakeLive:
        self.mirrors.append(FakeLive())
        return self.mirrors[-1]

    def control_ranges(self) -> dict[str, tuple]:
        return {"exposure_us": (100, 100_000), "gain": (1.0, 16.0)}


class FakeSampler(QtCore.QObject):
    sample = Signal(object)


class Sheet:
    """Scripted monitor sheet: view polls state, test sets it."""

    def __init__(self):
        self.state = OFF


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def bench(qapp):
    engine = FakeEngine()
    sampler = FakeSampler()
    sheet = Sheet()
    view = MonitorView(engine, sampler, lambda: sheet.state)
    return SimpleNamespace(engine=engine, sampler=sampler, sheet=sheet, view=view)


def test_viewfinder_wraps_mirror(bench):
    assert len(bench.engine.mirrors) == 1
    assert bench.engine.mirrors[0].parent() is bench.view.viewfinder_area
    assert bench.view.viewfinder_area.has_camera


def test_no_camera_means_no_mirror(qapp):
    engine = FakeEngine()
    engine.picam2 = None
    engine.current_mode = None
    view = MonitorView(engine, FakeSampler(), lambda: OFF)
    assert engine.mirrors == []
    assert not view.viewfinder_area.has_camera
    assert view.mode_btn.text() == " Mode: --"


def test_tick_reads_telemetry_into_strip_and_chips(bench):
    bench.engine.telemetry = telemetry(
        frame=42, fps=30.0, ExposureTime=20000, AnalogueGain=2.0, SensorTemperature=41.2
    )
    bench.view._tick()
    assert bench.view.status.telemetry_lbl.text() == "#42 (30.00 fps) exp 20000 ag 2.00"
    assert bench.view.status.temp_lbl.text() == "41.2\u00b0C"
    assert bench.view.mode_btn.text() == " Mode: 1920x1080 SRGGB12 30fps"
    chips = bench.view._chips
    assert chips["exposure_us"].text() == chip_text(CTRL_SPEC["exposure_us"], 20000, False)
    assert chips["gain"].text() == chip_text(CTRL_SPEC["gain"], 2.0, False)
    assert chips["colour_temp"].isHidden()


def test_metadata_gap_holds_last_reading(bench):
    bench.engine.telemetry = telemetry(frame=1, fps=30.0, ExposureTime=20000)
    bench.view._tick()
    bench.engine.telemetry = telemetry(frame=2, fps=30.0)
    bench.view._tick()
    assert bench.view._chips["exposure_us"].text() == chip_text(
        CTRL_SPEC["exposure_us"], 20000, False
    )


def test_manual_control_tints_chip(bench):
    bench.engine.control_state.gain = 4.0
    bench.view._tick()
    assert bench.view._chips["gain"].property("manual") is True
    bench.engine.control_state.gain = None
    bench.view._tick()
    assert bench.view._chips["gain"].property("manual") is False


def test_assists_follow_monitor_sheet(bench):
    bench.sheet.state = OFF._replace(peaking=True, zebra=True, zebra_threshold=0.8, histogram=True)
    bench.view._tick()
    area = bench.view.viewfinder_area
    assert bench.engine.mirrors[0].assists == (True, True, 0.8)
    assert area.findChild(HistogramOverlay).isVisibleTo(area)
    assert not area.findChild(FocusMapOverlay).isVisibleTo(area)
    bench.sheet.state = OFF
    bench.view._tick()
    assert bench.engine.mirrors[0].assists == (False, False, 0.95)
    assert not area.findChild(HistogramOverlay).isVisibleTo(area)


def test_focus_samples_reach_map_while_enabled(bench, monkeypatch):
    seen: list = []
    monkeypatch.setattr(focus_map.FocusMapOverlay, "set_levels", lambda _self, lv: seen.append(lv))
    heat = np.ones((8, 8))
    bench.sampler.sample.emit(SimpleNamespace(heat=heat))
    assert seen == []
    bench.sheet.state = OFF._replace(focus_map=True)
    bench.view.show()
    bench.view._tick()
    bench.sampler.sample.emit(SimpleNamespace(heat=heat))
    assert len(seen) == 1 and seen[0] is heat


def test_histogram_pushed_on_tick_when_shown(bench, monkeypatch):
    seen: list = []
    monkeypatch.setattr(HistogramOverlay, "set_histogram", lambda _self, bins: seen.append(bins))
    bench.engine.latest_histogram = np.arange(1024)
    bench.view.show()
    assert seen == []
    bench.sheet.state = OFF._replace(histogram=True)
    bench.view._tick()
    assert len(seen) == 1


def test_no_input_surface(bench):
    view = bench.view
    assert view.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    buttons = view.findChildren(QtWidgets.QPushButton)
    assert len(buttons) == len(CTRL_SPEC) + 1  # control chips plus the mode chip
    assert all(b.focusPolicy() == Qt.FocusPolicy.NoFocus for b in buttons)
    assert not any(b.isCheckable() for b in buttons)


def test_ticks_run_only_while_shown(bench):
    view = bench.view
    assert not view._tick_timer.isActive()
    view.show()
    assert view._tick_timer.isActive() and view._stats_timer.isActive()
    view.hide()
    assert not view._tick_timer.isActive() and not view._stats_timer.isActive()


def test_viewfinder_takes_width_under_strip_and_chips(bench, qapp):
    view = bench.view
    view.resize(1920, 1080)
    view.show()
    qapp.processEvents()
    width, height = view.viewfinder_area.lores_size()
    assert width == 1920
    assert 0 < height < 1080
