# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pane placement for panel only, monitor only and both heads, built offscreen."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

from camlab.gui.hybrid_root import HybridRoot, pane_screen, rect_text
from camlab.qt import QtCore, QtWidgets

PANEL = QtCore.QRect(0, 0, 800, 480)
MONITOR = QtCore.QRect(800, 0, 1920, 1080)
UNION = QtCore.QRect(0, 0, 2720, 1080)
MONITOR_ALONE = QtCore.QRect(0, 0, 1920, 1080)


def topology(panel=None, monitor=None, bounds=None) -> SimpleNamespace:
    return SimpleNamespace(panel=panel, monitor=monitor, bounds=bounds or QtCore.QRect())


BOTH = topology(PANEL, MONITOR, UNION)
PANEL_ONLY = topology(PANEL, None, PANEL)
MONITOR_ONLY = topology(None, MONITOR_ALONE, MONITOR_ALONE)


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class Bench:
    """Root plus the monitor views its factory handed out."""

    def __init__(self, forced: tuple[int, int] | None = None):
        self.made: list[QtWidgets.QWidget] = []
        self.root = HybridRoot(self._make_monitor, forced)
        self.root.show()

    def _make_monitor(self, parent: QtWidgets.QWidget) -> QtWidgets.QWidget:
        view = QtWidgets.QWidget(parent)
        self.made.append(view)
        return view

    def settle(self, size: tuple[int, int], topo: SimpleNamespace) -> HybridRoot:
        self.root.resize(*size)
        self.root.set_topology(topo)
        return self.root


@pytest.fixture
def bench(qapp) -> Bench:
    return Bench()


def test_panel_only_fills_window_without_monitor_view(bench):
    root = bench.settle((800, 480), PANEL_ONLY)
    assert root.panel_pane.geometry() == QtCore.QRect(0, 0, 800, 480)
    assert root.monitor_view is None
    assert bench.made == []


def test_monitor_only_puts_panel_ui_on_monitor(bench):
    root = bench.settle((1920, 1080), MONITOR_ONLY)
    assert root.panel_pane.geometry() == QtCore.QRect(0, 0, 1920, 1080)
    assert root.monitor_view is None


def test_both_places_panes_side_by_side(bench):
    root = bench.settle((2720, 1080), BOTH)
    assert root.panel_pane.geometry() == PANEL
    assert root.monitor_view is bench.made[0]
    assert root.monitor_view.geometry() == MONITOR
    assert root.monitor_view.isVisible()


def test_unplug_hides_monitor_view_and_replug_reuses_it(bench):
    root = bench.settle((2720, 1080), BOTH)
    bench.settle((800, 480), PANEL_ONLY)
    assert root.monitor_view.isHidden()
    assert root.panel_pane.geometry() == QtCore.QRect(0, 0, 800, 480)
    bench.settle((2720, 1080), BOTH)
    assert root.monitor_view.isVisible()
    assert len(bench.made) == 1


def test_no_screens_keeps_panel_pane_on_window(bench):
    root = bench.settle((640, 480), topology())
    assert root.panel_pane.geometry() == QtCore.QRect(0, 0, 640, 480)
    assert root.monitor_view is None


def test_rects_are_relative_to_bounds_origin(bench):
    shifted = topology(
        PANEL.translated(-800, 0), MONITOR.translated(-800, 0), UNION.translated(-800, 0)
    )
    root = bench.settle((2720, 1080), shifted)
    assert root.panel_pane.geometry() == PANEL
    assert root.monitor_view.geometry() == MONITOR


def test_resize_refits_single_pane(bench):
    root = bench.settle((800, 480), PANEL_ONLY)
    root.resize(1024, 600)
    assert root.panel_pane.geometry() == QtCore.QRect(0, 0, 1024, 600)


def test_monitor_view_stacks_below_panel_pane(bench):
    root = bench.settle((2720, 1080), BOTH)
    children = [c for c in root.children() if isinstance(c, QtWidgets.QWidget)]
    assert children.index(root.monitor_view) < children.index(root.panel_pane)


def test_forced_size_centres_panel_pane_and_skips_monitor(qapp):
    bench = Bench(forced=(800, 480))
    root = bench.settle((1920, 1080), BOTH)
    assert root.panel_pane.geometry() == QtCore.QRect(560, 300, 800, 480)
    assert root.monitor_view is None


def test_pane_screen_prefers_panel_then_bounds():
    assert pane_screen(BOTH) == PANEL
    assert pane_screen(MONITOR_ONLY) == MONITOR_ALONE
    assert pane_screen(topology()) is None


def test_rect_text():
    assert rect_text(MONITOR) == "1920x1080+800+0"
    assert rect_text(None) == "none"
