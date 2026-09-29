# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Application entry point: build the Qt app, capture, camera and main window."""

from __future__ import annotations

import logging
import os
import signal
import sys

from . import dmesg
from .camera import CameraEngine
from .config_manager import ConfigManager
from .display import Backlight, CursorPolicy, DisplayManager, apply_output_layout
from .dsi_panels import PanelRegistry
from .gl_viewfinder import install_gles_format
from .gui import fonts
from .gui.display_claim import DisplayClaim
from .gui.main_window import MainWindow
from .integrity import LOG_DATEFMT, LOG_FORMAT, LogClassifier, NullCapture, StderrCapture
from .modes import resolve_initial_mode
from .qt import QtCore, QtWidgets
from .sensors import SensorRegistry
from .settings import AwbMode, SettingsStore

log = logging.getLogger("camlab")


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
    logging.basicConfig(level=level, stream=sys.stderr, format=LOG_FORMAT, datefmt=LOG_DATEFMT)


def main(argv: list[str] | None = None) -> int:
    _setup_logging()

    # Splice stderr before libcamera/Picamera2 init so IPA child inherits it
    classifier = LogClassifier()
    capture = NullCapture() if os.environ.get("CAMLAB_NO_CAPTURE") else StderrCapture(classifier)

    registry = SensorRegistry.load()
    panels = PanelRegistry.load()
    config = ConfigManager()
    settings = SettingsStore()

    # open() only enumerates modes. Stream is configured below, once display size is known
    engine = CameraEngine()
    try:
        engine.open(camera_num=int(os.environ.get("CAMLAB_CAMERA_NUM", "0")))
    except Exception:
        log.exception("camera open failed")
        # Kernel probe failures behind a missing camera, libcamera reports none of them
        overlay = config.get_current()["overlay"]
        if overlay:
            for line in dmesg.read(overlay):
                capture.deliver(line)

    # Force native Wayland under Wayland session. picamera2 import sets xcb, which breaks
    # in-scene viewfinder (PyOpenGL needs EGL-current, Xwayland makes GLX-current)
    if os.environ.get("WAYLAND_DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "wayland"
        # Kiosk: no client-side decorations, even if fullscreen state drops
        os.environ["QT_WAYLAND_DISABLE_WINDOWDECORATION"] = "1"
    # Cage runs on a blank cursor theme to keep boot screen clean. Qt must not
    # inherit it, a real mouse needs a cursor it can see
    os.environ.pop("XCURSOR_PATH", None)

    # Settle outputs before Qt connects to compositor
    apply_output_layout(settings.get_display())

    # Restore persisted panel brightness before anything renders
    backlight = Backlight()
    saved_backlight = settings.get_backlight()
    if backlight.available and saved_backlight is not None:
        backlight.set_percent(saved_backlight)

    # Viewfinder needs a GLES context (samplerExternalOES), set before QApplication
    install_gles_format()
    app = QtWidgets.QApplication(argv if argv is not None else sys.argv)
    fonts.apply(app)

    # Boot mode: persisted selection when valid, else heaviest runnable mode
    if engine.picam2 is not None and engine.modes:
        overlay = config.get_current().get("overlay") or ""
        saved = settings.get_mode(overlay)
        mode, fps = resolve_initial_mode(engine.modes, saved)
        try:
            fixed = saved["fps_fixed"] if saved else True
            # Viewfinder size needs built window, so lores refits before camera start
            engine.configure_mode(mode, fps, (0, 0), fps_fixed=fixed)
            # Restore manual overrides after configure, so they clamp to the new ranges
            engine.set_control_state(**settings.get_controls(overlay))
            engine.set_grey_world(settings.get_awb() is AwbMode.GREY)
        except Exception:
            log.exception("camera configure failed")

    # CursorPolicy needs no handle: QApplication parentage keeps it alive
    display_manager = DisplayManager(app, settings.get_display)
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
    DisplayClaim(app, win.claim_display)
    display_manager.start()

    # Sent by ExecStop. Handler lands between bytecodes, so event loop does the write
    signal.signal(signal.SIGUSR1, lambda *_: QtCore.QTimer.singleShot(0, win.flush_settings))

    rc = app.exec()

    engine.stop()
    engine.close()
    capture.stop()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
