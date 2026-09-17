# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared log samples and fixtures faking DRM and input sysfs trees under tmp_path."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from camlab import drm
from camlab.integrity import LOG_DATEFMT, LOG_FORMAT

# dmesg -x after an ar0822 probe failure, device-tree chatter and a foreign driver included
DMESG_SAMPLE = """\
kern  :info  : [    0.036267] /axi/pcie@1000120000/rp1/i2c@88000/ar0822@10: Fixed dependency cycle(s) with /axi/pcie@1000120000/rp1/csi@110000
kern  :info  : [    0.036292] /axi/pcie@1000120000/rp1/csi@110000: Fixed dependency cycle(s) with /axi/pcie@1000120000/rp1/i2c@88000/ar0822@10
kern  :info  : [    4.037284] rp1-cfe 1f00110000.csi: found subdevice /axi/pcie@1000120000/rp1/i2c@88000/ar0822@10
kern  :warn  : [    4.100000] ar0822: loading out-of-tree module taints kernel.
kern  :err   : [    4.193195] ar0822 10-0010: Error reading reg 0x3000: -121
kern  :err   : [    4.193200] ar0822 10-0010: error -EREMOTEIO: Failed to read chip version
kern  :err   : [    4.199618] ar0822 10-0010: probe with driver ar0822 failed with error -121
kern  :err   : [    4.597354] edt_ft5x06 11-0038: Unable to fetch data, error: -121
"""

PROBE_FAILURE = [
    "[    4.037284] rp1-cfe 1f00110000.csi: found subdevice /axi/pcie@1000120000/rp1/i2c@88000/ar0822@10",
    "[    4.193195] ar0822 10-0010: Error reading reg 0x3000: -121",
    "[    4.193200] ar0822 10-0010: error -EREMOTEIO: Failed to read chip version",
    "[    4.199618] ar0822 10-0010: probe with driver ar0822 failed with error -121",
]

# The scrape carries the success notice for context, so only the rest are errors
SUBDEVICE_NOTICE = PROBE_FAILURE[0]
FAILURES = PROBE_FAILURE[1:]

# Lines the stderr capture already carries, none of them a kernel driver line
CAMERA_STACK = [
    "[0:32:24.379907628] [27725]  INFO Camera camera_manager.cpp:340 libcamera v0.7.1",
    "[2:03:04.000] [42] WARN V4L2 v4l2_videodevice.cpp:1906 /dev/video0[16:cap]: Dequeue timer",
    "00:00:00.059 [ERROR] [EGL] command: eglQueryDeviceStringEXT, error: EGL_BAD_PARAMETER",
    "00:00:00.328 [ERROR] [backend/drm/util.c:65] Failed to parse EDID",
    "12:50:03 ERROR camlab.camera: opened",
]


def logged(note: str) -> str:
    """Note as _setup_logging renders it, which is the form the panel classifies."""
    record = logging.LogRecord("camlab.camera", logging.WARNING, __file__, 1, note, None, None)
    return logging.Formatter(LOG_FORMAT, LOG_DATEFMT).format(record)


@pytest.fixture
def drm_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point camlab.drm at an empty tree (no connectors)."""
    root = tmp_path / "drm"
    monkeypatch.setattr(drm, "DRM_ROOT", root)
    return root


@pytest.fixture(autouse=True)
def _cold_dsi_display():
    """has_dsi_display caches, so keep the answer from leaking between tests."""
    drm.has_dsi_display.cache_clear()


@pytest.fixture
def fake_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Builder writing {event name: properties bitmask} as input sysfs dirs."""
    root = tmp_path / "input"
    monkeypatch.setattr(drm, "INPUT_ROOT", root)

    def build(devices: dict[str, str]) -> Path:
        for name, props in devices.items():
            d = root / name / "device"
            d.mkdir(parents=True, exist_ok=True)
            (d / "properties").write_text(f"{props}\n")
        return root

    return build


@pytest.fixture
def fake_drm(drm_root: Path):
    """Builder writing {connector name: status} as card1-* sysfs dirs."""

    def build(connectors: dict[str, str]) -> Path:
        for name, status in connectors.items():
            d = drm_root / f"card1-{name}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "status").write_text(f"{status}\n")
        return drm_root

    return build
