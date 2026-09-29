# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""MirrorView against stub engine, fake mirror and scripted monitor sheet state."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("PyQt6")

from conftest import FakeEngine, telemetry

from camlab.focus_metric import FocusSample
from camlab.gui import focus_map
from camlab.gui.focus_map import FocusMapOverlay
from camlab.gui.histogram import HistogramOverlay
from camlab.gui.mirror_view import MirrorView
from camlab.gui.style import COMPACT, REGULAR
from camlab.qt import Qt, QtCore, QtWidgets, Signal
from camlab.settings import MonitorState

OFF = MonitorState(
    histogram=False, focus_map=False, peaking=False, zebra=False, zebra_threshold=0.95
)
# Displays mirror can land on, DSI and monitor
PANEL = (800, 480)
MONITOR = (1920, 1080)


class FakeSampler(QtCore.QObject):
    sample = Signal(object)


class Sheet:
    """Scripted monitor sheet: view polls state, test sets it."""

    def __init__(self):
        self.state = OFF


@pytest.fixture
def bench(qapp):
    engine = FakeEngine()
    sampler = FakeSampler()
    sheet = Sheet()
    view = MirrorView(engine, sampler, lambda: sheet.state, REGULAR)
    return SimpleNamespace(engine=engine, sampler=sampler, sheet=sheet, view=view)


def tick(bench) -> None:
    """One chrome tick, which pushes snapshot here."""
    bench.view.update_status(bench.engine.telemetry)


def test_viewfinder_wraps_mirror(bench):
    assert len(bench.engine.mirrors) == 1
    assert bench.engine.mirrors[0].parent() is bench.view.viewfinder_area
    assert bench.view.viewfinder_area.has_camera


def test_no_camera_means_no_mirror(qapp):
    engine = FakeEngine()
    engine.picam2 = None
    engine.current_mode = None
    view = MirrorView(engine, FakeSampler(), lambda: OFF, REGULAR)
    assert engine.mirrors == []
    assert not view.viewfinder_area.has_camera


def test_density_follows_display_it_lands_on(qapp):
    """Mirror on DSI skins compact, as chrome would there."""
    engine = FakeEngine()
    engine.telemetry = telemetry(frame=1, fps=30.0, ExposureTime=20000)
    view = MirrorView(engine, FakeSampler(), lambda: OFF, COMPACT)
    view.update_status(engine.telemetry)
    assert view.profile is COMPACT
    assert view.status._compact
    # Compact drops frame counter, so strip itself reads density
    assert view.status.telemetry_lbl.text() == "30.00 fps exp 20000"


def test_screen_rect_reports_display_it_was_placed_on(qapp):
    """Chrome sizes lores off this, so mirror reports where it sits, not where it renders."""
    rect = QtCore.QRect(800, 0, *MONITOR)
    view = MirrorView(FakeEngine(), FakeSampler(), lambda: OFF, REGULAR, screen_rect=rect)
    assert view.screen_rect == rect


def test_tick_reads_telemetry_into_strip(bench):
    bench.engine.telemetry = telemetry(
        frame=42, fps=30.0, ExposureTime=20000, AnalogueGain=2.0, SensorTemperature=41.2
    )
    tick(bench)
    assert bench.view.status.telemetry_lbl.text() == "#42 (30.00 fps) exp 20000 ag 2.00"
    assert bench.view.status.temp_lbl.text() == "41.2\u00b0C"


def test_metadata_gap_holds_last_reading(bench):
    """Line holds while metadata drops keys across pipeline restart."""
    bench.engine.telemetry = telemetry(frame=1, fps=30.0, ExposureTime=20000)
    tick(bench)
    bench.engine.telemetry = telemetry(frame=2, fps=30.0)
    tick(bench)
    assert bench.view.status.telemetry_lbl.text() == "#2 (30.00 fps) exp 20000"


def test_assists_follow_monitor_sheet(bench):
    bench.sheet.state = OFF._replace(peaking=True, zebra=True, zebra_threshold=0.8, histogram=True)
    tick(bench)
    area = bench.view.viewfinder_area
    assert bench.engine.mirrors[0].assists == (True, True, 0.8)
    assert area.findChild(HistogramOverlay).isVisibleTo(area)
    assert not area.findChild(FocusMapOverlay).isVisibleTo(area)
    bench.sheet.state = OFF
    tick(bench)
    assert bench.engine.mirrors[0].assists == (False, False, 0.95)
    assert not area.findChild(HistogramOverlay).isVisibleTo(area)


def test_focus_samples_reach_map_while_enabled(bench, monkeypatch):
    seen: list = []
    monkeypatch.setattr(focus_map.FocusMapOverlay, "set_levels", lambda _self, lv: seen.append(lv))
    heat = np.ones((8, 8))
    bench.sampler.sample.emit(FocusSample(heat=heat))
    assert seen == []
    bench.sheet.state = OFF._replace(focus_map=True)
    bench.view.show()
    tick(bench)
    bench.sampler.sample.emit(FocusSample(heat=heat))
    assert len(seen) == 1 and seen[0] is heat


def test_histogram_pushed_on_tick_when_shown(bench, monkeypatch):
    seen: list = []
    monkeypatch.setattr(HistogramOverlay, "set_histogram", lambda _self, bins: seen.append(bins))
    bench.engine.latest_histogram = np.arange(1024)
    bench.view.show()
    assert seen == []
    bench.sheet.state = OFF._replace(histogram=True)
    tick(bench)
    assert len(seen) == 1


@pytest.mark.parametrize("profile", (COMPACT, REGULAR), ids=("compact", "regular"))
def test_nothing_on_mirror_invites_press(qapp, profile):
    """Mirror carries no controls, at either density since it swaps displays with chrome."""
    view = MirrorView(FakeEngine(), FakeSampler(), lambda: OFF, profile)
    assert view.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert view.findChildren(QtWidgets.QAbstractButton) == []
    assert all(
        w.cursor().shape() == Qt.CursorShape.ArrowCursor
        for w in view.viewfinder_area.findChildren(QtWidgets.QWidget)
    )


def test_mirror_stacks_strip_alone_over_picture(bench, qapp):
    """Chrome sizes mirror lores by subtracting strip hint alone, so third row here would
    steal picture camera never hears about."""
    view = bench.view
    root = view.layout()
    assert [root.itemAt(i).widget() for i in range(root.count())] == [
        view.status,
        view.viewfinder_area,
    ]
    view.resize(*MONITOR)
    view.show()
    qapp.processEvents()
    # Sizing reads hint, so layout must hand out exactly that
    assert view.status.height() == view.status.sizeHint().height()
    assert view.viewfinder_area.height() == MONITOR[1] - view.status.height()


@pytest.mark.parametrize(
    ("profile", "size"), ((COMPACT, PANEL), (REGULAR, MONITOR)), ids=("compact", "regular")
)
def test_picture_takes_room_controls_bar_held(qapp, profile, size):
    """Bar is gone on either display, so everything under strip is picture."""
    view = MirrorView(FakeEngine(), FakeSampler(), lambda: OFF, profile)
    view.resize(*size)
    view.show()
    qapp.processEvents()
    assert view.viewfinder_area.lores_size() == (size[0], size[1] - view.status.height())


def test_no_timer_of_its_own(bench):
    """Chrome drives every refresh, so second timer would put displays out of phase."""
    timers = bench.view.findChildren(
        QtCore.QTimer, options=Qt.FindChildOption.FindDirectChildrenOnly
    )
    assert timers == []


def test_board_stats_are_not_sampled_here(bench):
    """Chrome owns the one sampler, so twin must not read counters again."""
    assert not hasattr(bench.view, "_rpi_stats")
