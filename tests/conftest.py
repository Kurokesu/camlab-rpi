# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared log samples, engine stub, sysfs fakes under tmp_path and an offscreen window."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from camlab import config_manager, drm
from camlab.config_manager import ConfigManager
from camlab.dsi_panels import PanelRegistry
from camlab.gui import fonts, settings_dialog
from camlab.integrity import LOG_DATEFMT, LOG_FORMAT, LineSource, LogClassifier, NullCapture
from camlab.qt import QtWidgets
from camlab.sensors import SensorRegistry
from camlab.settings import SettingsStore

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


def logged(note: str, level: int = logging.WARNING) -> str:
    """Note as _setup_logging renders it, which is the form the panel classifies."""
    record = logging.LogRecord("camlab.camera", level, __file__, 1, note, None, None)
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


def telemetry(frame=None, fps=0.0, **metadata) -> SimpleNamespace:
    """One published frame as the views read it."""
    return SimpleNamespace(frame=frame, fps=fps, metadata=metadata)


class FakeLive(QtWidgets.QWidget):
    """Mirror stand-in, the GL widget no offscreen test can render."""

    def __init__(self):
        super().__init__()
        self.assists = None

    def set_assists(self, peaking: bool, zebra: bool, threshold: float) -> None:
        self.assists = (peaking, zebra, threshold)


class FakeEngine:
    """Engine the window and monitor view build against. Exposure and gain only, no WB chip."""

    def __init__(self):
        self.picam2 = object()
        self.telemetry = telemetry()
        self.control_state = SimpleNamespace(exposure_us=None, gain=None, colour_temp=None)
        # Compact chrome reads width and height, a monitor reads the label
        self.current_mode = SimpleNamespace(
            size=(1920, 1080), width=1920, height=1080, label=lambda: "1920x1080 SRGGB12 30fps"
        )
        self.latest_histogram = None
        self.mirrors: list[FakeLive] = []
        self.modes: list = []
        self.info: dict = {}

    def make_mirror(self) -> FakeLive:
        self.mirrors.append(FakeLive())
        return self.mirrors[-1]

    def make_viewfinder(self) -> QtWidgets.QWidget:
        return QtWidgets.QWidget()

    def control_ranges(self) -> dict[str, tuple]:
        return {"exposure_us": (100, 100_000), "gain": (1.0, 16.0)}

    def refit_lores(self, avail_size) -> bool:
        return False

    def set_stats_output(self, enabled: bool) -> None:
        pass

    def set_grey_world(self, enabled: bool) -> None:
        pass

    def on_first_frame(self, callback) -> None:
        pass

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass


@pytest.fixture(scope="session")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    fonts.apply(app)
    return app


PANEL_NAME = "Waveshare 43H"
PANEL_OVERLAY = "vc4-kms-dsi-7inch"


@pytest.fixture
def cm(tmp_path: Path, drm_root: Path, monkeypatch: pytest.MonkeyPatch) -> ConfigManager:
    """Empty config over tmp_path, no DRM connector and model reading as Pi 5."""
    monkeypatch.setattr(config_manager, "MODEL_PATH", tmp_path / "model")
    overlays = tmp_path / "overlays"
    overlays.mkdir()
    (overlays / f"{PANEL_OVERLAY}.dtbo").touch()
    return ConfigManager(config_path=tmp_path / "config.txt", overlays_dir=overlays)


@pytest.fixture
def build_win(qapp, cm: ConfigManager, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Builder for a shown MainWindow, reading config.txt as the test left it."""
    # picamera2 is absent on a CI runner, so this import waits until a test wants a window
    main_window = pytest.importorskip("camlab.gui.main_window")
    monkeypatch.delenv("CAMLAB_SCREEN", raising=False)
    monkeypatch.setattr(settings_dialog.network, "is_enabled", lambda: True)
    built = []

    def build(capture: LineSource | None = None):
        window = main_window.MainWindow(
            FakeEngine(),
            SensorRegistry.load(),
            PanelRegistry.load(),
            cm,
            capture or NullCapture(),
            LogClassifier(),
            SettingsStore(tmp_path / "state.json"),
        )
        window.resize(800, 480)
        window.show()
        qapp.processEvents()
        built.append(window)
        return window

    yield build
    for window in built:
        window._close_modal()
        window.close()


@pytest.fixture
def win(build_win):
    """Shown window over an empty config, so no panel is forced."""
    return build_win()
