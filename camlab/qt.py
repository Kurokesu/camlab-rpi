"""Single chokepoint for Qt imports and picamera2 preview widget.

The whole app imports Qt from here. PyQt6 throughout (Qt 6.8 Wayland backend
composites translucent native subsurfaces, which Qt 5.15 could not - that
enables sheets and cards floating over the GL preview with alpha). Phase 1
ran PyQt5; the migration kept this module as the import seam.
"""

from PyQt6 import QtCore, QtGui, QtWidgets  # noqa: F401

Qt = QtCore.Qt
Signal = QtCore.pyqtSignal
Slot = QtCore.pyqtSlot

BINDING_LABEL = "pyqt6/QGl6Picamera2"


def preview_widget_class(software=False):
    """Return the picamera2 Qt preview widget class for this binding."""
    # Deferred import: pulling in picamera2's preview machinery loads EGL, which
    # must not happen at camlab.qt import time (camera opens before QApplication).
    from picamera2.previews import qt as _p2qt
    return _p2qt.Q6Picamera2 if software else _p2qt.QGl6Picamera2
