# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""UI density, log button tint, boot backlog replay and the sensor card the window opens."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from conftest import FAILURES, PANEL_NAME, PANEL_OVERLAY, logged

main_window = pytest.importorskip("camlab.gui.main_window")

from camlab import config_manager
from camlab.gui.style import COMPACT, REGULAR, build_stylesheet
from camlab.integrity import IntegrityStats, NullCapture
from camlab.qt import QtCore

PANEL_RECT = QtCore.QRect(0, 0, 800, 480)
MONITOR_RECT = QtCore.QRect(0, 0, 1920, 1080)
PANEL_ONLY = SimpleNamespace(panel=PANEL_RECT, monitor=None, bounds=PANEL_RECT)
MONITOR_ONLY = SimpleNamespace(panel=None, monitor=MONITOR_RECT, bounds=MONITOR_RECT)


def sensor_card(win):
    """Card the Sensor button opens over whatever the test left in config.txt."""
    win._choose_sensor()
    return win._overlay.card


@pytest.fixture
def calls(win, monkeypatch):
    """Config write and both power calls recorded instead of run."""
    seen: list[str] = []
    monkeypatch.setattr(win.config, "apply", lambda *_a: seen.append("write"))
    monkeypatch.setattr(main_window, "poweroff", lambda: seen.append("poweroff"))
    monkeypatch.setattr(main_window, "reboot", lambda: seen.append("reboot"))
    return seen


def test_profile_follows_pane_screen_across_display_switch(win):
    """Accessor follows the pane screen, matching the profile the window skinned with."""
    win._on_topology_changed(MONITOR_ONLY)
    assert win.profile is REGULAR
    assert win.styleSheet() == build_stylesheet(REGULAR)
    win._on_topology_changed(PANEL_ONLY)
    assert win.profile is COMPACT
    assert win.styleSheet() == build_stylesheet(COMPACT)


@pytest.mark.parametrize(
    ("stats", "sev"),
    [
        (IntegrityStats({"error": {"app": 1}}), "error"),
        (IntegrityStats({"warning": {"app": 8}}), ""),
        (IntegrityStats({"warning": {"stack_pairing": 1}}), "warning"),
        (IntegrityStats({"warning": {"app": 8, "stack_pairing": 1}}), "warning"),
    ],
)
def test_log_button_tint_skips_app_warnings(win, stats, sev):
    """App errors tint, app warnings do not, and volume does not override category."""
    win._on_integrity(stats)
    assert win._sev == sev


@pytest.mark.parametrize(
    "line",
    [logged("camera open failed: no camera enumerated by libcamera", logging.ERROR), FAILURES[0]],
)
def test_records_predating_window_reach_panel_and_tally(build_win, line):
    """Camera open and the scrape behind it both predate the window, replay carries them."""
    capture = NullCapture()
    capture.deliver(line)
    win = build_win(capture)
    win.monitor._emit()
    # View renders HTML, which collapses the run of spaces dmesg pads timestamps with
    assert " ".join(line.split()) in win.log_panel.view.toPlainText()
    assert win.log_panel.filter.button("error").text() == "Errors 1"
    assert win._sev == "error"


def test_auto_detected_display_locks_claimed_csi_port(win, fake_drm):
    """Firmware brought the panel up, so claimed port and display row both stay put."""
    fake_drm({"DSI-1": "connected"})
    card = sensor_card(win)
    assert not card.port_sel.button("cam0").isEnabled()
    assert not card.display_sel.button(None).isEnabled()
    assert card.wiring_note.text() == "cam0 is used by the auto-detected touch display"


def test_compute_module_dsi_leaves_csi_ports_selectable(win, fake_drm):
    """CM carrier DSI is not tied to a CSI port, so a live connector blocks neither."""
    fake_drm({"DSI-1": "connected"})
    config_manager.MODEL_PATH.write_text("Raspberry Pi Compute Module 5 Rev 1.0")
    card = sensor_card(win)
    assert card.port_sel.button("cam0").isEnabled()
    assert card.display_sel.button(None).isEnabled()
    assert card.wiring_note.text() == ""


def test_configured_display_block_does_not_read_as_auto_detected(win, cm):
    """Operator wrote that block, so the panel moves to the connector the camera leaves."""
    cm._rewrite_display_in_place(PANEL_OVERLAY)  # claims cam1 next boot
    card = sensor_card(win)
    assert card.display_sel.current_value() == PANEL_NAME
    assert card.wiring_note.text() == "Camera on CAM/DISP1, touch display on CAM/DISP0"
    assert card.port_sel.button("cam1").isEnabled()


def test_off_catalog_display_block_keeps_claimed_port(win, cm):
    """Unknown overlay is written back as-is, so the claimed port stays out of reach."""
    (cm.overlays_dir / "vc4-kms-dsi-generic.dtbo").touch()
    cm._rewrite_display_in_place("vc4-kms-dsi-generic,dsi0")  # claims cam0
    card = sensor_card(win)
    assert card.display_sel.current_value() == "vc4-kms-dsi-generic,dsi0"
    assert not card.port_sel.button("cam0").isEnabled()


def test_sensor_card_offers_reboot_left_of_shutdown_and_cancel_takes_enter(win):
    """Reboot sits left of Shutdown as on the power card, and Enter still reaches Cancel."""
    card = sensor_card(win)
    assert card.reboot_btn.text() == "Apply && Reboot"
    assert card.reboot_btn.x() < card.apply_btn.x()
    assert card.primary_button.text() == "Cancel"


@pytest.mark.parametrize(("attr", "call"), [("reboot_btn", "reboot"), ("apply_btn", "poweroff")])
def test_each_apply_writes_config_before_its_power_action(win, calls, attr, call):
    getattr(sensor_card(win), attr).click()
    assert calls == ["write", call]


def test_neither_apply_is_live_until_selection_changes(win, cm):
    """Operator opening the card to look must not be one press from a power cycle."""
    (cm.overlays_dir / "imx477.dtbo").touch()
    cm._rewrite_in_place("imx477", "cam1", [])
    card = sensor_card(win)
    assert not card.reboot_btn.isEnabled() and not card.apply_btn.isEnabled()
    card.port_sel.button("cam0").click()
    assert card.reboot_btn.isEnabled() and card.apply_btn.isEnabled()


def test_sensor_card_footer_fits_compact_panel(win):
    """Three footer buttons beside the rewire warning still fit 800x480 touch panel."""
    win._on_topology_changed(PANEL_ONLY)
    assert win.profile is COMPACT
    card = sensor_card(win)
    inset = win._overlay.layout().contentsMargins()
    avail = win._overlay.width() - inset.left() - inset.right()
    assert card.sizeHint().width() <= avail
