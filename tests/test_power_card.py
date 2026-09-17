# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Power card on a real MainWindow built offscreen, reached from Escape and power button."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PyQt6")
main_window = pytest.importorskip("camlab.gui.main_window")

from test_monitor_view import FakeEngine

from camlab.config_manager import ConfigManager
from camlab.dsi_panels import PanelRegistry
from camlab.gui import settings_dialog
from camlab.integrity import LogClassifier, NullCapture
from camlab.qt import QtWidgets
from camlab.sensors import SensorRegistry
from camlab.settings import SettingsStore


class Engine(FakeEngine):
    """Monitor view stub plus what MainWindow itself reaches for."""

    def __init__(self):
        super().__init__()
        self.modes: list = []
        self.info: dict = {}

    def make_viewfinder(self) -> QtWidgets.QWidget:
        return QtWidgets.QWidget()

    def set_stats_output(self, enabled: bool) -> None:
        pass

    def on_first_frame(self, callback) -> None:
        pass

    def set_grey_world(self, enabled: bool) -> None:
        pass

    def refit_lores(self, avail_size) -> bool:
        return False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def win(qapp, tmp_path: Path, drm_root, monkeypatch):
    """Shown window over empty config, so no panel is forced and no probe runs."""
    monkeypatch.delenv("CAMLAB_SCREEN", raising=False)
    monkeypatch.setattr(settings_dialog.network, "is_enabled", lambda: True)
    window = main_window.MainWindow(
        Engine(),
        SensorRegistry.load(),
        PanelRegistry.load(),
        ConfigManager(config_path=tmp_path / "config.txt", overlays_dir=tmp_path),
        NullCapture(),
        LogClassifier(),
        SettingsStore(tmp_path / "state.json"),
    )
    window.resize(800, 480)
    window.show()
    qapp.processEvents()
    yield window
    window._close_modal()
    window.close()


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
    return {b.text(): b for b in card.findChildren(QtWidgets.QPushButton)}


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
