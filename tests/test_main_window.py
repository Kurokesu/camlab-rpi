# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Kernel driver lines, boot backlog, Log button tint and sensor card wiring.

MainWindow is too heavy to build here, so each method runs unbound against a stub.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("picamera2")
pytest.importorskip("PyQt6")

from conftest import PROBE_FAILURE

from camlab import config_manager, dmesg
from camlab.config_manager import ConfigManager
from camlab.dsi_panels import PanelRegistry
from camlab.gui.main_window import MainWindow
from camlab.gui.sensor_dialog import SensorCard
from camlab.integrity import APP_CATEGORY, IntegrityStats, LineSource
from camlab.qt import QtWidgets
from camlab.sensors import SensorRegistry


def stub(overlay: str, model: str = "") -> SimpleNamespace:
    shown: list[str] = []
    fed: list[str] = []
    return SimpleNamespace(
        config=SimpleNamespace(get_current=lambda: {"overlay": overlay}),
        engine=SimpleNamespace(info={"Model": model} if model else {}),
        log_panel=SimpleNamespace(append_line=shown.append),
        monitor=SimpleNamespace(feed=fed.append),
        shown=shown,
        fed=fed,
    )


def wire_stub(capture: LineSource) -> SimpleNamespace:
    """What _wire reaches for, capture real and every other signal inert."""
    inert = SimpleNamespace(connect=lambda *_: None)
    shown: list[str] = []
    fed: list[str] = []
    return SimpleNamespace(
        capture=capture,
        log_panel=SimpleNamespace(append_line=shown.append, update_integrity=None, cleared=inert),
        monitor=SimpleNamespace(feed=fed.append, stats_changed=inert, reset=None),
        status=SimpleNamespace(stats_tapped=inert),
        viewfinder_area=SimpleNamespace(tapped=inert, toggle_stats_overlay=None),
        engine=SimpleNamespace(on_first_frame=lambda _cb: None),
        _on_integrity=None,
        _on_viewfinder_tapped=None,
        _on_first_frame=None,
        shown=shown,
        fed=fed,
    )


def tint_stub() -> SimpleNamespace:
    """What _on_integrity reaches for, the button restyle inert."""
    return SimpleNamespace(
        log_btn=SimpleNamespace(isChecked=lambda: False),
        _sync_log_button=lambda _checked: None,
    )


@pytest.mark.parametrize(
    ("stats", "sev"),
    [
        (IntegrityStats({"error": {APP_CATEGORY: 1}}), "error"),
        (IntegrityStats({"warning": {APP_CATEGORY: 8}}), ""),
        (IntegrityStats({"warning": {"stack_pairing": 1}}), "warning"),
        (IntegrityStats({"warning": {APP_CATEGORY: 8, "stack_pairing": 1}}), "warning"),
    ],
)
def test_log_button_tint_skips_app_warnings(stats, sev):
    """App errors tint, app warnings do not, and volume does not override category."""
    win = tint_stub()
    MainWindow._on_integrity(win, stats)
    assert win._sev == sev


def test_early_records_replay_once_panel_exists():
    """Camera open runs before the window, so its error reaches panel and tally on replay."""
    capture = LineSource()
    line = "12:50:03 ERROR camlab: camera open failed: no camera enumerated by libcamera"
    capture._deliver(line)
    win = wire_stub(capture)
    MainWindow._wire(win)
    assert win.shown == [line]
    assert win.fed == [line]


def test_missing_camera_pushes_driver_lines(monkeypatch):
    monkeypatch.setattr(dmesg, "read", lambda module: PROBE_FAILURE)
    win = stub("ar0822")
    MainWindow._report_driver_errors(win)
    assert win.shown == PROBE_FAILURE
    assert win.fed == PROBE_FAILURE


def test_scrape_asks_for_selected_overlay(monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr(dmesg, "read", lambda module: asked.append(module) or [])
    MainWindow._report_driver_errors(stub("imx585"))
    assert asked == ["imx585"]


@pytest.mark.parametrize(
    ("overlay", "model"),
    [("ar0822", "ar0822"), ("", "")],
)
def test_scrape_stays_off_without_no_camera_path(monkeypatch, overlay, model):
    monkeypatch.setattr(dmesg, "read", lambda module: pytest.fail("scraped the ring buffer"))
    win = stub(overlay, model)
    MainWindow._report_driver_errors(win)
    assert win.shown == []


PANEL_NAME = "Waveshare 43H"
PANEL_OVERLAY = "vc4-kms-dsi-7inch"


class SensorWindow(SimpleNamespace):
    """What _choose_sensor reaches for, the card kept instead of shown."""

    _is_mono = staticmethod(MainWindow._is_mono)
    _display_name_current = MainWindow._display_name_current
    _choose_sensor = MainWindow._choose_sensor

    def _open_modal(self, card) -> None:
        self.card = card

    def _apply_sensor(self, *_args) -> None:
        """Qt rejects a None slot, so Apply and Cancel both need a callable."""

    _close_modal = _apply_sensor


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def cm(tmp_path: Path, drm_root: Path, monkeypatch: pytest.MonkeyPatch) -> ConfigManager:
    """Empty config over tmp_path, no DRM connector and model reading as Pi 5."""
    monkeypatch.setattr(config_manager, "MODEL_PATH", tmp_path / "model")
    overlays = tmp_path / "overlays"
    overlays.mkdir()
    (overlays / f"{PANEL_OVERLAY}.dtbo").touch()
    return ConfigManager(config_path=tmp_path / "config.txt", overlays_dir=overlays)


@pytest.fixture
def open_card(qapp, cm: ConfigManager):
    """Builder for the sensor card over whatever the test left in config.txt."""

    def build() -> SensorCard:
        win = SensorWindow(registry=SensorRegistry.load(), panels=PanelRegistry.load(), config=cm)
        win._choose_sensor()
        return win.card

    return build


def test_auto_detected_display_locks_claimed_csi_port(cm, open_card, fake_drm):
    """Firmware brought the panel up, so claimed port and display row both stay put."""
    fake_drm({"DSI-1": "connected"})
    card = open_card()
    assert not card.port_sel.button("cam0").isEnabled()
    assert not card.display_sel.button(None).isEnabled()
    assert card.wiring_note.text() == "cam0 is used by the auto-detected touch display"


def test_compute_module_dsi_leaves_csi_ports_selectable(cm, open_card, fake_drm):
    """CM carrier DSI is not tied to a CSI port, so a live connector blocks neither."""
    fake_drm({"DSI-1": "connected"})
    config_manager.MODEL_PATH.write_text("Raspberry Pi Compute Module 5 Rev 1.0")
    card = open_card()
    assert card.port_sel.button("cam0").isEnabled()
    assert card.display_sel.button(None).isEnabled()
    assert card.wiring_note.text() == ""


def test_configured_display_block_does_not_read_as_auto_detected(cm, open_card):
    """Operator wrote that block, so the panel moves to the connector the camera leaves."""
    cm._rewrite_display_in_place(PANEL_OVERLAY)  # claims cam1 next boot
    card = open_card()
    assert card.display_sel.current_value() == PANEL_NAME
    assert card.wiring_note.text() == "Camera on CAM/DISP1, touch display on CAM/DISP0"
    assert card.port_sel.button("cam1").isEnabled()


def test_off_catalog_display_block_keeps_claimed_port(cm, open_card):
    """Unknown overlay is written back as-is, so the claimed port stays out of reach."""
    (cm.overlays_dir / "vc4-kms-dsi-generic.dtbo").touch()
    cm._rewrite_display_in_place("vc4-kms-dsi-generic,dsi0")  # claims cam0
    card = open_card()
    assert card.display_sel.current_value() == "vc4-kms-dsi-generic,dsi0"
    assert not card.port_sel.button("cam0").isEnabled()
