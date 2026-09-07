# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""ViewfinderArea around a pre-made live widget."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")

from camlab.gui.viewfinder_area import ViewfinderArea
from camlab.qt import Qt, QtCore, QtGui, QtWidgets


class FakeEngine:
    def __init__(self):
        self.picam2 = object()
        self.current_mode = None
        self.viewfinders = 0

    def make_viewfinder(self) -> QtWidgets.QWidget:
        self.viewfinders += 1
        return QtWidgets.QWidget()


class FakeLive(QtWidgets.QWidget):
    """Mirror stand-in with the two hooks ViewfinderArea reaches for."""

    def __init__(self):
        super().__init__()
        self.frosted = None
        self.assists = None

    def set_frosted(self, frosted: bool) -> None:
        self.frosted = frosted

    def set_assists(self, peaking: bool, zebra: bool, threshold: float) -> None:
        self.assists = (peaking, zebra, threshold)


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_pre_made_live_widget_skips_make_viewfinder(qapp):
    engine = FakeEngine()
    live = FakeLive()
    area = ViewfinderArea(engine, live=live)
    assert engine.viewfinders == 0
    assert live.parent() is area
    assert area.has_camera


def test_assists_and_frost_reach_live_widget(qapp):
    live = FakeLive()
    area = ViewfinderArea(FakeEngine(), live=live)
    area.set_assists(True, False, 0.9)
    area.set_frost(True)
    assert live.assists == (True, False, 0.9)
    assert live.frosted is True


def test_default_constructor_makes_viewfinder_and_taps(qapp):
    engine = FakeEngine()
    area = ViewfinderArea(engine)
    taps: list[int] = []
    area.tapped.connect(lambda: taps.append(1))
    area.mousePressEvent(
        QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QPointF(5, 5),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )
    assert engine.viewfinders == 1
    assert taps == [1]
