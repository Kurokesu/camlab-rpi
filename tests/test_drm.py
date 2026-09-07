# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""DRM sysfs parsing and DSI to CSI port mapping."""

from __future__ import annotations

from camlab import drm


def test_no_tree_reads_empty(drm_root):
    assert drm.connected_connectors() == set()
    assert drm.has_dsi_connector() is False
    assert drm.dsi_blocked_ports() == set()


def test_connected_connectors_skip_disconnected(fake_drm):
    fake_drm({"HDMI-A-1": "connected", "DSI-2": "disconnected"})
    assert drm.connected_connectors() == {"HDMI-A-1"}


def test_has_dsi_connector_ignores_status(fake_drm):
    # DSI has no hotplug detect, presence of the connector is what counts.
    fake_drm({"DSI-2": "disconnected"})
    assert drm.has_dsi_connector() is True


def test_dsi_display_needs_touchscreen(fake_drm, fake_input):
    # Bare connector with no panel wired. A mouse reads 0, a pointer prop reads 1.
    fake_drm({"DSI-2": "connected"})
    fake_input({"event0": "0", "event1": "1"})
    assert drm.has_dsi_display() is False


def test_dsi_display_with_touchscreen(fake_drm, fake_input):
    fake_drm({"DSI-2": "connected"})
    fake_input({"event0": "0", "event8": "2"})
    assert drm.has_dsi_display() is True


def test_touchscreen_alone_is_not_a_dsi_display(fake_drm, fake_input):
    fake_drm({"HDMI-A-1": "connected"})
    fake_input({"event8": "2"})
    assert drm.has_dsi_display() is False


def test_malformed_properties_ignored(fake_drm, fake_input):
    fake_drm({"DSI-2": "connected"})
    fake_input({"event0": "junk"})
    assert drm.has_dsi_display() is False


def test_dsi_display_survives_touch_readd(fake_drm, fake_input):
    # Applying the calibration matrix re-adds the device. A live re-read would
    # flip to False and blank the panel.
    fake_drm({"DSI-2": "connected"})
    root = fake_input({"event8": "2"})
    assert drm.has_dsi_display() is True
    (root / "event8" / "device" / "properties").unlink()
    assert drm.has_dsi_display() is True


def test_dsi1_blocks_cam0(fake_drm):
    fake_drm({"DSI-1": "connected"})
    assert drm.dsi_blocked_ports() == {"cam0"}


def test_dsi2_blocks_cam1_disconnected_ignored(fake_drm):
    fake_drm({"DSI-2": "connected", "DSI-1": "disconnected"})
    assert drm.dsi_blocked_ports() == {"cam1"}


def test_out_of_range_dsi_index_ignored(fake_drm):
    fake_drm({"DSI-3": "connected"})
    assert drm.dsi_blocked_ports() == set()
