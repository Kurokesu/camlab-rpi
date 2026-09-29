# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pane placement for DSI only, monitor only and Both, built offscreen."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

from camlab.gui.screen_root import ScreenRoot, chrome_screen, mirror_screen, rect_text
from camlab.qt import QtCore, QtWidgets

DSI = QtCore.QRect(0, 0, 800, 480)
MONITOR = QtCore.QRect(800, 0, 1920, 1080)
UNION = QtCore.QRect(0, 0, 2720, 1080)
MONITOR_ALONE = QtCore.QRect(0, 0, 1920, 1080)


def topology(dsi=None, monitor=None, bounds=None) -> SimpleNamespace:
    return SimpleNamespace(dsi=dsi, monitor=monitor, bounds=bounds or QtCore.QRect())


BOTH = topology(DSI, MONITOR, UNION)
DSI_ONLY = topology(DSI, None, DSI)
MONITOR_ONLY = topology(None, MONITOR_ALONE, MONITOR_ALONE)


class Bench:
    """Root plus mirror views its factory handed out."""

    def __init__(self, forced: tuple[int, int] | None = None):
        self.made: list[QtWidgets.QWidget] = []
        self.root = ScreenRoot(self._make_mirror_view, forced)
        self.root.show()

    def _make_mirror_view(self, parent: QtWidgets.QWidget) -> QtWidgets.QWidget:
        view = QtWidgets.QWidget(parent)
        self.made.append(view)
        return view

    def settle(self, size: tuple[int, int], topo: SimpleNamespace) -> ScreenRoot:
        self.root.resize(*size)
        self.root.set_topology(topo)
        return self.root


@pytest.fixture
def bench(qapp) -> Bench:
    return Bench()


def test_dsi_only_fills_window_without_mirror_view(bench):
    root = bench.settle((800, 480), DSI_ONLY)
    assert root.chrome_pane.geometry() == QtCore.QRect(0, 0, 800, 480)
    assert root.mirror_view is None
    assert bench.made == []


def test_monitor_only_puts_chrome_on_monitor(bench):
    root = bench.settle((1920, 1080), MONITOR_ONLY)
    assert root.chrome_pane.geometry() == QtCore.QRect(0, 0, 1920, 1080)
    assert root.mirror_view is None


def test_both_puts_chrome_on_monitor_and_mirror_on_dsi(bench):
    root = bench.settle((2720, 1080), BOTH)
    assert root.chrome_pane.geometry() == MONITOR
    assert root.mirror_view is bench.made[0]
    assert root.mirror_view.geometry() == DSI
    assert root.mirror_view.isVisible()


def test_unplug_hides_mirror_view_and_replug_reuses_it(bench):
    root = bench.settle((2720, 1080), BOTH)
    bench.settle((800, 480), DSI_ONLY)
    assert root.mirror_view.isHidden()
    assert root.chrome_pane.geometry() == QtCore.QRect(0, 0, 800, 480)
    bench.settle((2720, 1080), BOTH)
    assert root.mirror_view.isVisible()
    assert len(bench.made) == 1


def test_no_screens_keeps_chrome_pane_on_window(bench):
    root = bench.settle((640, 480), topology())
    assert root.chrome_pane.geometry() == QtCore.QRect(0, 0, 640, 480)
    assert root.mirror_view is None


def test_rects_are_relative_to_bounds_origin(bench):
    shifted = topology(
        DSI.translated(-800, 0), MONITOR.translated(-800, 0), UNION.translated(-800, 0)
    )
    root = bench.settle((2720, 1080), shifted)
    assert root.chrome_pane.geometry() == MONITOR
    assert root.mirror_view.geometry() == DSI


def test_resize_refits_single_pane(bench):
    root = bench.settle((800, 480), DSI_ONLY)
    root.resize(1024, 600)
    assert root.chrome_pane.geometry() == QtCore.QRect(0, 0, 1024, 600)


def test_mirror_view_stacks_below_chrome_pane(bench):
    root = bench.settle((2720, 1080), BOTH)
    children = [c for c in root.children() if isinstance(c, QtWidgets.QWidget)]
    assert children.index(root.mirror_view) < children.index(root.chrome_pane)


def test_forced_size_centers_chrome_pane_and_skips_mirror(qapp):
    bench = Bench(forced=(800, 480))
    root = bench.settle((1920, 1080), BOTH)
    assert root.chrome_pane.geometry() == QtCore.QRect(560, 300, 800, 480)
    assert root.mirror_view is None


def test_claimed_dsi_swaps_panes(bench):
    """Chrome follows display just used, so mirror takes the one it left."""
    root = bench.settle((2720, 1080), BOTH)
    root.set_chrome_on_dsi(True)
    assert root.chrome_pane.geometry() == DSI
    assert root.mirror_view.geometry() == MONITOR
    root.set_chrome_on_dsi(False)
    assert root.chrome_pane.geometry() == MONITOR
    assert root.mirror_view.geometry() == DSI
    # One mirror throughout, since second would reset texture stream live view shares
    assert len(bench.made) == 1


def test_claim_with_one_display_lit_leaves_pane_alone(bench):
    root = bench.settle((1920, 1080), MONITOR_ONLY)
    root.set_chrome_on_dsi(True)
    assert root.chrome_pane.geometry() == QtCore.QRect(0, 0, 1920, 1080)
    assert root.mirror_view is None


def test_chrome_screen_prefers_monitor_then_dsi_then_bounds():
    assert chrome_screen(BOTH) == MONITOR
    assert chrome_screen(MONITOR_ONLY) == MONITOR_ALONE
    assert chrome_screen(DSI_ONLY) == DSI
    assert chrome_screen(topology()) is None


def test_claim_only_decides_where_both_displays_are_lit():
    """Chrome lands on whatever is lit, so stale claim cannot dark-screen app."""
    assert (chrome_screen(BOTH, True), mirror_screen(BOTH, True)) == (DSI, MONITOR)
    assert (chrome_screen(BOTH, False), mirror_screen(BOTH, False)) == (MONITOR, DSI)
    assert chrome_screen(MONITOR_ONLY, True) == MONITOR_ALONE
    assert chrome_screen(DSI_ONLY, False) == DSI
    # Mirror only takes display chrome left free, so one lit display has none
    assert mirror_screen(MONITOR_ONLY, True) is None
    assert mirror_screen(DSI_ONLY, False) is None


def test_rect_text():
    assert rect_text(MONITOR) == "1920x1080+800+0"
    assert rect_text(None) == "none"
