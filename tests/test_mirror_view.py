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
DSI = (800, 480)
MONITOR = (1920, 1080)


class FakeSampler(QtCore.QObject):
    sample = Signal(object)


class Sheet:
    """Scripted monitor sheet: view polls state, test sets it."""

    def __init__(self):
        self.state = OFF


@pytest.fixture
def harness(qapp):
    engine = FakeEngine()
    sampler = FakeSampler()
    sheet = Sheet()
    view = MirrorView(engine, sampler, lambda: sheet.state, REGULAR)
    return SimpleNamespace(engine=engine, sampler=sampler, sheet=sheet, view=view)


def tick(harness) -> None:
    """One chrome tick, which pushes snapshot here."""
    harness.view.update_status(harness.engine.telemetry)


def test_viewfinder_wraps_mirror(harness):
    assert len(harness.engine.mirrors) == 1
    assert harness.engine.mirrors[0].parent() is harness.view.viewfinder_area
    assert harness.view.viewfinder_area.has_camera


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


def test_tick_reads_telemetry_into_strip(harness):
    harness.engine.telemetry = telemetry(
        frame=42, fps=30.0, ExposureTime=20000, AnalogueGain=2.0, SensorTemperature=41.2
    )
    tick(harness)
    assert harness.view.status.telemetry_lbl.text() == "#42 (30.00 fps) exp 20000 ag 2.00"
    assert harness.view.status.temp_lbl.text() == "41.2\u00b0C"


def test_metadata_gap_holds_last_reading(harness):
    """Line holds while metadata drops keys across pipeline restart."""
    harness.engine.telemetry = telemetry(frame=1, fps=30.0, ExposureTime=20000)
    tick(harness)
    harness.engine.telemetry = telemetry(frame=2, fps=30.0)
    tick(harness)
    assert harness.view.status.telemetry_lbl.text() == "#2 (30.00 fps) exp 20000"


def test_assists_follow_monitor_sheet(harness):
    harness.sheet.state = OFF._replace(
        peaking=True, zebra=True, zebra_threshold=0.8, histogram=True
    )
    tick(harness)
    area = harness.view.viewfinder_area
    assert harness.engine.mirrors[0].assists == (True, True, 0.8)
    assert area.findChild(HistogramOverlay).isVisibleTo(area)
    assert not area.findChild(FocusMapOverlay).isVisibleTo(area)
    harness.sheet.state = OFF
    tick(harness)
    assert harness.engine.mirrors[0].assists == (False, False, 0.95)
    assert not area.findChild(HistogramOverlay).isVisibleTo(area)


def test_focus_samples_reach_map_while_enabled(harness, monkeypatch):
    seen: list = []
    monkeypatch.setattr(focus_map.FocusMapOverlay, "set_levels", lambda _self, lv: seen.append(lv))
    heat = np.ones((8, 8))
    harness.sampler.sample.emit(FocusSample(heat=heat))
    assert seen == []
    harness.sheet.state = OFF._replace(focus_map=True)
    harness.view.show()
    tick(harness)
    harness.sampler.sample.emit(FocusSample(heat=heat))
    assert len(seen) == 1 and seen[0] is heat


def test_histogram_pushed_on_tick_when_shown(harness, monkeypatch):
    seen: list = []
    monkeypatch.setattr(HistogramOverlay, "set_histogram", lambda _self, bins: seen.append(bins))
    harness.engine.latest_histogram = np.arange(1024)
    harness.view.show()
    assert seen == []
    harness.sheet.state = OFF._replace(histogram=True)
    tick(harness)
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


def test_mirror_stacks_strip_alone_over_picture(harness, qapp):
    """Chrome sizes mirror lores by subtracting strip hint alone, so third row here would
    steal picture camera never hears about."""
    view = harness.view
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
    ("profile", "size"), ((COMPACT, DSI), (REGULAR, MONITOR)), ids=("compact", "regular")
)
def test_picture_takes_room_controls_bar_held(qapp, profile, size):
    """Bar is gone on either display, so everything under strip is picture."""
    view = MirrorView(FakeEngine(), FakeSampler(), lambda: OFF, profile)
    view.resize(*size)
    view.show()
    qapp.processEvents()
    assert view.viewfinder_area.lores_size() == (size[0], size[1] - view.status.height())


def test_no_timer_of_its_own(harness):
    """Chrome drives every refresh, so second timer would put displays out of phase."""
    timers = harness.view.findChildren(
        QtCore.QTimer, options=Qt.FindChildOption.FindDirectChildrenOnly
    )
    assert timers == []


def test_board_stats_are_not_sampled_here(harness):
    """Chrome owns the one sampler, so twin must not read counters again."""
    assert not hasattr(harness.view, "_rpi_stats")
