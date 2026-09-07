# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Settings card Display and Auto WB rows, built offscreen without camera or compositor."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

from camlab.gui import settings_dialog
from camlab.gui.settings_dialog import SettingsCard
from camlab.qt import QtWidgets
from camlab.settings import AwbMode, DisplayMode, SettingsStore

DISPLAY_LABELS = ["External", "Built-in", "Both"]
AWB_LABELS = ["Grey world", "libcamera"]


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "state.json")


@pytest.fixture
def panel_rig(qapp, monkeypatch):
    """DSI display attached, networking up, layout writes recorded instead of run."""
    applied: list[DisplayMode] = []
    monkeypatch.setattr(settings_dialog, "has_dsi_display", lambda: True)
    monkeypatch.setattr(settings_dialog.network, "is_enabled", lambda: True)
    monkeypatch.setattr(settings_dialog, "apply_output_layout", applied.append)
    return applied


@pytest.fixture
def monitor_only(qapp, monkeypatch):
    """No built-in display and networking up, so only the always-on rows build."""
    monkeypatch.setattr(settings_dialog, "has_dsi_display", lambda: False)
    monkeypatch.setattr(settings_dialog.network, "is_enabled", lambda: True)


def _card(store: SettingsStore, on_grey_world=lambda _on: None) -> SettingsCard:
    return SettingsCard(
        store,
        backlight_pct=None,
        on_apply_network=lambda _on: None,
        on_backlight=lambda _pct: True,
        on_grey_world=on_grey_world,
        on_cancel=lambda: None,
    )


def _labels(card: QtWidgets.QWidget) -> list[str]:
    return [lbl.text() for lbl in card.findChildren(QtWidgets.QLabel)]


def test_display_row_lists_modes_and_reflects_store(panel_rig, store):
    store.set_display(DisplayMode.BUILTIN)
    card = _card(store)
    assert "Display:" in _labels(card)
    segments = card.display_sel.findChildren(QtWidgets.QPushButton)
    assert [b.text() for b in segments] == DISPLAY_LABELS
    assert card.display_sel.current_value() is DisplayMode.BUILTIN
    assert panel_rig == []


def test_display_pick_persists_then_applies(panel_rig, store, monkeypatch):
    card = _card(store)
    seen: list[tuple[DisplayMode, DisplayMode]] = []
    monkeypatch.setattr(
        settings_dialog,
        "apply_output_layout",
        lambda mode: seen.append((mode, store.get_display())),
    )
    card.display_sel.button(DisplayMode.BOTH).click()
    assert seen == [(DisplayMode.BOTH, DisplayMode.BOTH)]


def test_display_row_absent_on_monitor_only_rig(panel_rig, store, monkeypatch):
    monkeypatch.setattr(settings_dialog, "has_dsi_display", lambda: False)
    card = _card(store)
    assert "Display:" not in _labels(card)
    assert panel_rig == []


def test_awb_row_lists_algorithms_and_reflects_store(monitor_only, store):
    store.set_awb(AwbMode.LIBCAMERA)
    card = _card(store)
    assert "Auto WB:" in _labels(card)
    segments = card.awb_sel.findChildren(QtWidgets.QPushButton)
    assert [b.text() for b in segments] == AWB_LABELS
    assert card.awb_sel.current_value() is AwbMode.LIBCAMERA


def test_awb_row_defaults_to_grey_world(monitor_only, store):
    assert _card(store).awb_sel.current_value() is AwbMode.GREY


def test_awb_pick_persists_then_applies(monitor_only, store):
    seen: list[tuple[bool, AwbMode]] = []
    card = _card(store, on_grey_world=lambda on: seen.append((on, store.get_awb())))
    card.awb_sel.button(AwbMode.LIBCAMERA).click()
    card.awb_sel.button(AwbMode.GREY).click()
    assert seen == [(False, AwbMode.LIBCAMERA), (True, AwbMode.GREY)]
