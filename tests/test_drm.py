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


def test_dsi1_blocks_cam0(fake_drm):
    fake_drm({"DSI-1": "connected"})
    assert drm.dsi_blocked_ports() == {"cam0"}


def test_dsi2_blocks_cam1_disconnected_ignored(fake_drm):
    fake_drm({"DSI-2": "connected", "DSI-1": "disconnected"})
    assert drm.dsi_blocked_ports() == {"cam1"}


def test_out_of_range_dsi_index_ignored(fake_drm):
    fake_drm({"DSI-3": "connected"})
    assert drm.dsi_blocked_ports() == set()
