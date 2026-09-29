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
from camlab.gui.mirror_view import MirrorView
from camlab.gui.status_strip import StatusStrip
from camlab.gui.style import COMPACT, REGULAR, build_stylesheet
from camlab.integrity import IntegrityStats, NullCapture
from camlab.qt import QtCore, QtWidgets

DSI_RECT = QtCore.QRect(0, 0, 800, 480)
MONITOR_RECT = QtCore.QRect(0, 0, 1920, 1080)
DSI_ONLY = SimpleNamespace(dsi=DSI_RECT, monitor=None, bounds=DSI_RECT)
MONITOR_ONLY = SimpleNamespace(dsi=None, monitor=MONITOR_RECT, bounds=MONITOR_RECT)
BOTH = SimpleNamespace(
    dsi=DSI_RECT,
    monitor=QtCore.QRect(800, 0, 1920, 1080),
    bounds=QtCore.QRect(0, 0, 2720, 1080),
)


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


def test_profile_follows_chrome_screen_across_display_switch(win):
    """Accessor follows the pane screen, matching the profile the window skinned with."""
    win._on_topology_changed(MONITOR_ONLY)
    assert win.profile is REGULAR
    assert win.styleSheet() == build_stylesheet(REGULAR)
    win._on_topology_changed(DSI_ONLY)
    assert win.profile is COMPACT
    assert win.styleSheet() == build_stylesheet(COMPACT)
    # Chrome takes monitor whenever there is one, so Both skins regular
    win._on_topology_changed(BOTH)
    assert win.profile is REGULAR


def test_backlight_slider_offered_whenever_dsi_is_lit(win):
    """Chrome sits on monitor in Both mode, so density no longer tracks lit DSI."""
    win._backlight = SimpleNamespace(available=True, get_percent=lambda: 60)
    offered = {}
    for name, topology in (("dsi", DSI_ONLY), ("both", BOTH), ("monitor", MONITOR_ONLY)):
        win._on_topology_changed(topology)
        win._open_settings()
        offered[name] = hasattr(win._overlay.card, "backlight_slider")
        win._close_modal()
    assert offered == {"dsi": True, "both": True, "monitor": False}


def test_one_board_sample_feeds_both_strips(win, monkeypatch):
    """Sampling per strip would let the two disagree, loads are deltas over each own phase."""
    win._on_topology_changed(BOTH)
    seen: list[tuple[object, dict]] = []
    monkeypatch.setattr(
        StatusStrip, "set_rpi_stats", lambda self, texts: seen.append((self, texts))
    )
    win._sample_rpi()
    assert [strip for strip, _ in seen] == [win.status, win._root.mirror_view.status]
    assert seen[0][1] is seen[1][1]


def test_mirror_is_addressed_only_while_lit(win):
    """One guard for every reader, so none of them can disagree about the mirror."""
    assert win._live_mirror is None
    win._on_topology_changed(BOTH)
    assert win._live_mirror is win._root.mirror_view
    win._on_topology_changed(DSI_ONLY)
    assert win._live_mirror is None
    win._on_topology_changed(BOTH)
    assert win._live_mirror is win._root.mirror_view


def test_touch_hands_chrome_to_dsi_and_mouse_takes_it_back(win):
    """Mouse stays on monitor and touch on DSI, so press names where chrome belongs."""
    win._on_topology_changed(BOTH)
    mirror = win._live_mirror
    assert win.claim_display(True) is True
    assert win._root.chrome_pane.geometry() == DSI_RECT
    assert win.profile is COMPACT
    # Mirror takes display chrome left, dressed for it
    assert (mirror.screen_rect, mirror.profile) == (BOTH.monitor, REGULAR)
    # Already there, so press is operator working a control, not a claim
    assert win.claim_display(True) is False
    assert win.claim_display(False) is True
    assert win._root.chrome_pane.geometry() == BOTH.monitor
    assert win.profile is REGULAR
    assert (mirror.screen_rect, mirror.profile) == (DSI_RECT, COMPACT)
    # Same mirror throughout, since second would reset texture stream live view shares
    assert win._live_mirror is mirror


def test_claim_needs_both_displays_lit(win):
    """One display carries chrome whatever was pressed, so nothing moves."""
    win._on_topology_changed(MONITOR_ONLY)
    assert win.claim_display(True) is False
    assert win.profile is REGULAR


def test_plugging_display_hands_chrome_back_to_monitor(win):
    """Fresh start puts it there, so hotplug should not leave it where finger did."""
    win._on_topology_changed(BOTH)
    win.claim_display(True)
    win._on_topology_changed(DSI_ONLY)
    win._on_topology_changed(BOTH)
    assert win._root.chrome_pane.geometry() == BOTH.monitor
    assert win.profile is REGULAR
    mirror = win._live_mirror
    assert (mirror.screen_rect, mirror.profile) == (DSI_RECT, COMPACT)


def test_one_telemetry_snapshot_reaches_mirror(win, monkeypatch):
    """Mirror ticking itself left displays on frames up to a tick apart."""
    win._on_topology_changed(BOTH)
    seen: list = []
    monkeypatch.setattr(MirrorView, "update_status", lambda _self, t: seen.append(t))
    win._update_status()
    assert len(seen) == 1 and seen[0] is win.engine.telemetry


def test_lores_size_holds_across_layout_caught_mid_settle(win):
    """Sizing from live widgets bought second camera reconfigure over a few pixels."""
    win._on_topology_changed(MONITOR_ONLY)
    settled = win._lores_avail()
    # Resize burst mid-switch: rows and viewfinder still carry outgoing layout
    win.status.resize(win.status.width(), win.status.height() + 20)
    win.viewfinder_area.resize(640, 360)
    assert win._lores_avail() == settled


def test_lores_size_matches_settled_layout(win, qapp, monkeypatch):
    """Derived size has to be what settled layout hands viewfinder, not near it."""
    monkeypatch.setattr(win, "_apply_fullscreen", lambda: None)  # offscreen screen is not 1080p
    win._on_topology_changed(MONITOR_ONLY)
    win.resize(MONITOR_RECT.width(), MONITOR_RECT.height())
    qapp.processEvents()
    assert win._lores_avail() == win.viewfinder_area.lores_size()


def test_mirror_lores_matches_what_mirror_stacks(win, qapp, monkeypatch):
    """Mirror branch subtracts mirror strip alone, so row added there must reach this sum
    too or camera streams size picture cannot use."""
    monkeypatch.setattr(win, "_apply_fullscreen", lambda: None)  # offscreen screen is not 1080p
    win._on_topology_changed(BOTH)
    win.resize(BOTH.bounds.width(), BOTH.bounds.height())
    qapp.processEvents()
    mirror = win._live_mirror
    avail = main_window._pane_avail(DSI_RECT, mirror.status)
    assert mirror.viewfinder_area.lores_size() == avail


def test_lores_size_covers_larger_display(win):
    """Lores never upscales on either display, so Both sizes to monitor."""
    win._on_topology_changed(BOTH)
    width, height = win._lores_avail()
    assert width == BOTH.monitor.width()
    assert DSI_RECT.height() < height < BOTH.monitor.height()


def test_claim_leaves_lores_size_alone(win):
    """Size change restarts camera, so every tap moving chrome froze picture. Size is
    monitor carrying mirror, which chrome pane never exceeds."""
    win._on_topology_changed(BOTH)
    monitor_mirror = main_window._pane_avail(BOTH.monitor, win.status)
    sizes = [win._lores_avail()]
    for on_dsi in (True, False):
        win.claim_display(on_dsi)
        sizes.append(win._lores_avail())
    assert sizes == [monitor_mirror] * 3


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


def test_failed_reboot_from_sensor_card_is_reported(win, calls, monkeypatch):
    """Config is already rewritten, so a refused reboot has to reach the operator."""

    def refused() -> None:
        raise RuntimeError("sudo: a password is required")

    monkeypatch.setattr(main_window, "reboot", refused)
    sensor_card(win).reboot_btn.click()
    labels = [lbl.text() for lbl in win._overlay.card.findChildren(QtWidgets.QLabel)]
    assert "Reboot failed" in labels
    assert calls == ["write"]


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
    win._on_topology_changed(DSI_ONLY)
    assert win.profile is COMPACT
    card = sensor_card(win)
    inset = win._overlay.layout().contentsMargins()
    avail = win._overlay.width() - inset.left() - inset.right()
    assert card.sizeHint().width() <= avail
