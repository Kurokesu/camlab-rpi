# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Power card on a real MainWindow built offscreen, reached from Escape and power button."""

from __future__ import annotations

import pytest

main_window = pytest.importorskip("camlab.gui.main_window")

from camlab.qt import Qt, QtCore, QtGui, QtWidgets


@pytest.fixture
def calls(monkeypatch):
    """Both power calls and settings flush recorded instead of run."""
    seen: list[str] = []
    monkeypatch.setattr(main_window, "poweroff", lambda: seen.append("poweroff"))
    monkeypatch.setattr(main_window, "reboot", lambda: seen.append("reboot"))
    monkeypatch.setattr(
        main_window.MainWindow, "flush_settings", lambda _self: seen.append("flush")
    )
    return seen


def buttons(card) -> dict[str, QtWidgets.QPushButton]:
    """Keyed on label. Tile label is a child widget, so tile name comes off accessibleName."""
    return {b.text() or b.accessibleName(): b for b in card.findChildren(QtWidgets.QPushButton)}


def press(widget, pos: QtCore.QPoint) -> None:
    """Mouse press and release at pos, which is the delivery click() skips."""
    for kind in (QtCore.QEvent.Type.MouseButtonPress, QtCore.QEvent.Type.MouseButtonRelease):
        QtWidgets.QApplication.sendEvent(
            widget,
            QtGui.QMouseEvent(
                kind,
                QtCore.QPointF(pos),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            ),
        )


def test_escape_offers_reboot_next_to_shutdown_and_cancel_takes_enter(win, calls):
    win._on_escape()
    card = win._overlay.card
    assert list(buttons(card)) == ["Reboot", "Shutdown", "Cancel"]
    assert card.primary_button.text() == "Cancel"
    card.primary_button.click()
    assert win._overlay is None and calls == []
    # Power button opens the same card, second Escape closes it
    win.shutdown_btn.click()
    assert win._overlay is not None
    win._on_escape()
    assert win._overlay is None and calls == []


@pytest.mark.parametrize(("label", "call"), [("Reboot", "reboot"), ("Shutdown", "poweroff")])
def test_each_choice_flushes_settings_then_runs(win, calls, label, call):
    win._on_escape()
    buttons(win._overlay.card)[label].click()
    assert calls == ["flush", call]
    assert win._overlay is None


def test_press_over_glyph_and_label_reaches_tile(win, calls):
    """Tile children are transparent to mouse events, so neither swallows a press."""
    win._on_escape()
    tile = buttons(win._overlay.card)["Reboot"]
    centers = [lbl.geometry().center() for lbl in tile.findChildren(QtWidgets.QLabel)]
    assert len(centers) == 2
    assert [tile.childAt(pos) for pos in centers] == [None, None]
    press(tile, centers[0])
    assert calls == ["flush", "reboot"]


def test_failed_action_is_reported_and_app_stays_up(win, calls, monkeypatch):
    def refused() -> None:
        raise RuntimeError("sudo: a password is required")

    monkeypatch.setattr(main_window, "reboot", refused)
    win._on_escape()
    buttons(win._overlay.card)["Reboot"].click()
    labels = [lbl.text() for lbl in win._overlay.card.findChildren(QtWidgets.QLabel)]
    assert "Reboot failed" in labels
    assert calls == ["flush"]


def test_escape_closes_log_panel_before_offering_power_actions(win, calls):
    win.log_btn.setChecked(True)
    win._on_escape()
    assert not win.log_btn.isChecked() and win._overlay is None
    win._on_escape()
    assert win._overlay is not None and calls == []
