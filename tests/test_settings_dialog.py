# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Settings card Display row, built offscreen without camera or compositor."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")

from camlab.gui import settings_dialog
from camlab.gui.settings_dialog import SettingsCard
from camlab.qt import QtWidgets
from camlab.settings import DisplayMode, SettingsStore

DISPLAY_LABELS = ["External", "Built-in", "Both"]


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


def _card(store: SettingsStore) -> SettingsCard:
    return SettingsCard(
        store,
        backlight_pct=None,
        on_apply_network=lambda _on: None,
        on_backlight=lambda _pct: True,
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
