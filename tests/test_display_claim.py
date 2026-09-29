# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Display claim filter: device behind press names display it claims."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from camlab.gui.display_claim import DisplayClaim
from camlab.qt import Qt, QtCore, QtGui, QtWidgets


class FakeApp(QtCore.QObject):
    """QApplication stand-in, so no filter stays on real one."""

    def __init__(self):
        super().__init__()
        self.filters: list[QtCore.QObject] = []

    def installEventFilter(self, obj) -> None:
        self.filters.append(obj)


class FakeDevice:
    def __init__(self, kind):
        self._kind = kind

    def type(self):
        return self._kind


class FakeEvent:
    def __init__(self, device, kind=QtCore.QEvent.Type.MouseButtonPress):
        self._device = device
        self._kind = kind

    def type(self):
        return self._kind

    def pointingDevice(self):
        return self._device


class Claims:
    """Records each display offered, answering whether chrome moved."""

    def __init__(self, moved: bool = True):
        self.offered: list[bool] = []
        self.moved = moved

    def __call__(self, on_dsi: bool) -> bool:
        self.offered.append(on_dsi)
        return self.moved


MOUSE = FakeDevice(QtGui.QInputDevice.DeviceType.Mouse)
TOUCH = FakeDevice(QtGui.QInputDevice.DeviceType.TouchScreen)


def filter_over(claims: Claims) -> DisplayClaim:
    app = FakeApp()
    DisplayClaim(app, claims)
    (installed,) = app.filters
    return installed


def press() -> QtGui.QMouseEvent:
    """Real press, carrying primary pointing device rather than touchscreen."""
    return QtGui.QMouseEvent(
        QtCore.QEvent.Type.MouseButtonPress,
        QtCore.QPointF(5, 5),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def test_touch_claims_dsi_and_mouse_monitor():
    """Mouse stays on monitor and touch on DSI, so device is whole decision."""
    claims = Claims()
    claim = filter_over(claims)
    claim.eventFilter(None, FakeEvent(TOUCH))
    claim.eventFilter(None, FakeEvent(MOUSE))
    assert claims.offered == [True, False]


def test_anything_but_press_is_left_alone():
    claims = Claims()
    claim = filter_over(claims)
    moved = FakeEvent(TOUCH, QtCore.QEvent.Type.MouseMove)
    assert claim.eventFilter(None, moved) is False
    assert claim.eventFilter(None, FakeEvent(None)) is False
    assert claims.offered == []


def test_press_that_moves_chrome_never_works_control_under_it(qapp):
    """Controls arrive under finger already down."""
    claims = Claims(moved=True)
    claim = DisplayClaim(qapp, claims)
    btn = QtWidgets.QPushButton()
    btn.resize(50, 20)
    seen: list[bool] = []
    btn.pressed.connect(lambda: seen.append(True))
    try:
        QtWidgets.QApplication.sendEvent(btn, press())
        assert claims.offered == [False] and seen == []
        # Chrome already there, so press is operator working a control
        claims.moved = False
        QtWidgets.QApplication.sendEvent(btn, press())
        assert seen == [True]
    finally:
        qapp.removeEventFilter(claim)


def test_failing_claim_is_swallowed_once_rather_than_aborting(qapp, caplog):
    """Exception escaping Qt virtual would abort PyQt."""

    def boom(_on_dsi: bool) -> bool:
        raise RuntimeError("topology went away")

    claim = filter_over(boom)
    assert claim.eventFilter(None, FakeEvent(MOUSE)) is False
    assert claim.eventFilter(None, FakeEvent(TOUCH)) is False
    failures = [r for r in caplog.records if "display claim" in r.getMessage()]
    assert len(failures) == 1
