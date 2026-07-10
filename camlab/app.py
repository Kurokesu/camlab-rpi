"""Application entry point: build the Qt app, capture, camera and main window."""

from __future__ import annotations

import logging
import os
import sys

from .camera import CameraEngine
from .config_manager import ConfigManager
from .gl_viewfinder import install_gles_format
from .gui.main_window import MainWindow
from .integrity import LogClassifier, NullCapture, StderrCapture
from .modes import DEFAULT_DISPLAY_MAX_FPS, resolve_initial_mode
from .qt import QtWidgets
from .sensors import SensorRegistry
from .settings import SettingsStore

log = logging.getLogger("camlab")

# Estimated non-viewfinder chrome height (status strip + controls row) used to size
# lores stream before the window is laid out. Runtime mode changes use the
# viewfinder widget's real size instead.
_CHROME_PX = 90


def _display_limits(app) -> tuple[float, tuple[int, int]]:
    """(display_max_fps, viewfinder_avail_size) derived from the primary screen.

    display_max_fps is capped at the bench ceiling (60) unless overridden via
    CAMLAB_DISPLAY_MAX_FPS. avail size is the screen minus estimated chrome.
    """
    screen = app.primaryScreen()
    geo = screen.geometry() if screen else None
    avail = (geo.width(), max(1, geo.height() - _CHROME_PX)) if geo else (1280, 720)

    override = os.environ.get("CAMLAB_DISPLAY_MAX_FPS")
    if override:
        try:
            return float(override), avail
        except ValueError:
            log.warning("ignoring bad CAMLAB_DISPLAY_MAX_FPS=%r", override)
    rate = screen.refreshRate() if screen else 0.0
    rate = round(rate) if rate and rate >= 1 else DEFAULT_DISPLAY_MAX_FPS
    return min(float(rate), DEFAULT_DISPLAY_MAX_FPS), avail


_LEVELS = {
    "trace": logging.DEBUG, "debug": logging.DEBUG, "info": logging.INFO,
    "warn": logging.WARNING, "warning": logging.WARNING, "error": logging.ERROR,
    "off": logging.CRITICAL + 10,
}


def _setup_logging() -> None:
    level = _LEVELS.get(os.environ.get("CAMLAB_LOG_LEVEL", "info").lower(), logging.INFO)
    logging.basicConfig(
        level=level, stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S")


def main(argv: list[str] | None = None) -> int:
    _setup_logging()

    # Splice stderr BEFORE libcamera/Picamera2 init so the IPA child inherits it.
    # StderrCapture is a plain QObject and is safe to build before QApplication.
    capture = NullCapture() if os.environ.get("CAMLAB_NO_CAPTURE") else StderrCapture()
    classifier = LogClassifier()

    registry = SensorRegistry.load()
    config = ConfigManager()
    settings = SettingsStore()

    # open() only enumerates modes. The stream is configured below once the
    # display size is known.
    engine = CameraEngine()
    try:
        engine.open(camera_num=int(os.environ.get("CAMLAB_CAMERA_NUM", "0")))
    except Exception as exc:
        log.error("camera open failed: %s", exc)

    # Run natively on Wayland under a Wayland session (e.g. Cage). Importing
    # picamera2 force-sets QT_QPA_PLATFORM=xcb there, which is poison for the
    # in-scene viewfinder: its PyOpenGL calls need the Qt context EGL-current,
    # and under Xwayland it is GLX-current (xcb also flashes the window at its
    # X11 size before fullscreening).
    if os.environ.get("WAYLAND_DISPLAY"):
        os.environ["QT_QPA_PLATFORM"] = "wayland"

    # Viewfinder needs a GLES context (samplerExternalOES), set before QApplication.
    install_gles_format()
    app = QtWidgets.QApplication(argv if argv is not None else sys.argv)

    display_max_fps, avail = _display_limits(app)

    # Resolve and configure the boot mode: a valid persisted selection, else the
    # heaviest runnable mode (max-stress default). Single configure at boot.
    if engine.picam2 is not None and engine.modes:
        overlay = config.get_current().get("overlay") or ""
        mode, fps = resolve_initial_mode(
            engine.modes, settings.get_mode(overlay), display_max_fps)
        try:
            engine.configure_mode(mode, fps, avail)
            # Restore persisted manual overrides. Must follow configure so
            # they clamp against the new mode's ranges.
            engine.set_control_state(**settings.get_controls(overlay))
        except Exception as exc:
            log.error("camera configure failed: %s", exc)

    win = MainWindow(engine, registry, config, capture, classifier,
                     settings, display_max_fps)
    win.showFullScreen()

    # The camera is started by the window once it reaches fullscreen (see
    # MainWindow). Starting it here, before the event loop runs, would block with
    # the window still mapped at its initial size and look like a boot glitch.

    rc = app.exec()

    engine.stop()
    engine.close()
    capture.stop()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
