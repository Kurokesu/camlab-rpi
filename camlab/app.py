# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Application entry point: build the Qt app, capture, camera and main window."""

from __future__ import annotations

import logging
import os
import sys

from .camera import CameraEngine
from .config_manager import ConfigManager
from .display import Backlight, CursorPolicy, DisplayManager, enforce_output_policy
from .dsi_panels import PanelRegistry
from .gl_viewfinder import install_gles_format
from .gui.main_window import MainWindow
from .gui.style import profile_for_screen
from .integrity import LogClassifier, NullCapture, StderrCapture
from .modes import resolve_initial_mode
from .qt import QtWidgets
from .sensors import SensorRegistry
from .settings import SettingsStore

log = logging.getLogger("camlab")

# Chrome height (status strip + controls row) sizing the boot lores stream.
# Errors are free, the stream refits to the real viewfinder before camera start.
_CHROME_PX = 90
_CHROME_COMPACT_PX = 85


def _avail_size(app) -> tuple[int, int]:
    """Viewfinder area estimate (screen minus chrome) for boot lores sizing."""
    screen = app.primaryScreen()
    geo = screen.geometry() if screen else None
    if geo is None:
        return (1280, 720)
    chrome = _CHROME_COMPACT_PX if profile_for_screen(screen).compact else _CHROME_PX
    return (geo.width(), max(1, geo.height() - chrome))


_LEVELS = {
    "trace": logging.DEBUG,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
    "off": logging.CRITICAL + 10,
}


def _setup_logging() -> None:
    level = _LEVELS.get(os.environ.get("CAMLAB_LOG_LEVEL", "info").lower(), logging.INFO)
    logging.basicConfig(
        level=level,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main(argv: list[str] | None = None) -> int:
    _setup_logging()

    # Splice stderr before libcamera/Picamera2 init so IPA child inherits it.
    capture = NullCapture() if os.environ.get("CAMLAB_NO_CAPTURE") else StderrCapture()
    classifier = LogClassifier()

    registry = SensorRegistry.load()
    panels = PanelRegistry.load()
    config = ConfigManager()
    settings = SettingsStore()

    # open() only enumerates modes. Stream is configured below, once display size is known.
    engine = CameraEngine()
    try:
        engine.open(camera_num=int(os.environ.get("CAMLAB_CAMERA_NUM", "0")))
    except Exception as exc:  # noqa: BLE001
        log.error("camera open failed: %s", exc)

    # Force native Wayland under Wayland session. picamera2 import sets xcb, which breaks
    # in-scene viewfinder (PyOpenGL needs EGL-current, Xwayland makes GLX-current).
    if os.environ.get("WAYLAND_DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "wayland"
        # Kiosk: no client-side decorations, even if fullscreen state drops.
        os.environ["QT_WAYLAND_DISABLE_WINDOWDECORATION"] = "1"

    # Settle HDMI versus DSI before Qt connects to the compositor, so layout profile
    # and lores sizing see the final display.
    enforce_output_policy()

    # Restore persisted panel brightness before anything renders.
    backlight = Backlight()
    saved_backlight = settings.get_backlight()
    if backlight.available and saved_backlight is not None:
        backlight.set_percent(saved_backlight)

    # Viewfinder needs a GLES context (samplerExternalOES), set before QApplication.
    install_gles_format()
    app = QtWidgets.QApplication(argv if argv is not None else sys.argv)

    avail = _avail_size(app)

    # Boot mode: persisted selection when valid, else heaviest runnable mode.
    # One configure at boot.
    if engine.picam2 is not None and engine.modes:
        overlay = config.get_current().get("overlay") or ""
        saved = settings.get_mode(overlay)
        mode, fps = resolve_initial_mode(engine.modes, saved)
        try:
            engine.configure_mode(mode, fps, avail, fps_fixed=saved["fps_fixed"] if saved else True)
            # Restore manual overrides after configure, so they clamp to the new ranges.
            engine.set_control_state(**settings.get_controls(overlay))
        except Exception as exc:  # noqa: BLE001
            log.error("camera configure failed: %s", exc)

    # CursorPolicy needs no handle: QApplication parentage keeps it alive.
    display_manager = DisplayManager(app)
    CursorPolicy(app)

    win = MainWindow(
        engine,
        registry,
        panels,
        config,
        capture,
        classifier,
        settings,
        display_manager=display_manager,
        backlight=backlight,
    )
    win.showFullScreen()
    display_manager.start()

    # Window starts camera once fullscreen. Starting here blocks before event loop runs.

    rc = app.exec()

    engine.stop()
    engine.close()
    capture.stop()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
