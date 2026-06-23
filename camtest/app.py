"""Application entry point: build the Qt app, capture, camera, and main window."""

from __future__ import annotations

import logging
import os
import sys

from . import qt
from .camera import CameraEngine
from .config_manager import ConfigManager
from .integrity import LogClassifier, NullCapture, StderrCapture
from .qt import QtWidgets
from .sensors import SensorRegistry

log = logging.getLogger("camtest")

_LEVELS = {
    "trace": logging.DEBUG, "debug": logging.DEBUG, "info": logging.INFO,
    "warn": logging.WARNING, "warning": logging.WARNING, "error": logging.ERROR,
    "off": logging.CRITICAL + 10,
}


def _setup_logging() -> None:
    level = _LEVELS.get(os.environ.get("CAMTEST_LOG_LEVEL", "info").lower(), logging.INFO)
    logging.basicConfig(
        level=level, stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S")


def main(argv: list[str] | None = None) -> int:
    _setup_logging()

    # Splice stderr BEFORE libcamera/Picamera2 init so the IPA child inherits it.
    # StderrCapture is a plain QObject and is safe to build before QApplication.
    capture = NullCapture() if os.environ.get("CAMTEST_NO_CAPTURE") else StderrCapture()
    classifier = LogClassifier()

    registry = SensorRegistry.load()
    config = ConfigManager()

    # Open the camera BEFORE QApplication (matches the working Phase 1 proto). Doing
    # it after QApplication lets Xwayland's EGL init first and the picamera2 GL
    # preview surface then fails with EGL_BAD_ALLOC.
    size = tuple(int(x) for x in os.environ.get("CAMTEST_PREVIEW_SIZE", "1280x720").lower().split("x"))
    engine = CameraEngine(size=size)
    try:
        engine.open(camera_num=int(os.environ.get("CAMTEST_CAMERA_NUM", "0")))
    except Exception as exc:
        log.error("camera open failed: %s", exc)

    app = QtWidgets.QApplication(argv if argv is not None else sys.argv)

    binding_label = f"{qt.BINDING}/{'QGlPicamera2' if qt.BINDING == 'pyqt5' else 'QGl6Picamera2'}"

    from .gui.main_window import MainWindow
    win = MainWindow(engine, registry, config, capture, classifier, binding_label)
    win.showFullScreen()

    if engine.picam2 is not None:
        try:
            engine.start()
        except Exception as exc:
            log.error("camera start failed: %s", exc)

    rc = app.exec_() if hasattr(app, "exec_") else app.exec()

    engine.stop()
    engine.close()
    capture.stop()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
