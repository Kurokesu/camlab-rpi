"""Single chokepoint for the Qt binding + the picamera2 preview widget.

The whole app imports Qt from here so switching bindings (PyQt5 <-> PyQt6) is a
one-file change. Phase 1 settled on PyQt5 / QGlPicamera2 (Debian-recommended,
verified working under Cage->Xwayland on this CM5). Override with
CAMTEST_QT_BINDING=pyqt6 if ever needed.
"""

import os

BINDING = os.environ.get("CAMTEST_QT_BINDING", "pyqt5").lower()

if BINDING == "pyqt6":
    from PyQt6 import QtCore, QtGui, QtWidgets  # noqa: F401
    _GL_WIDGET = "QGl6Picamera2"
    _SW_WIDGET = "Q6Picamera2"
else:
    from PyQt5 import QtCore, QtGui, QtWidgets  # noqa: F401
    _GL_WIDGET = "QGlPicamera2"
    _SW_WIDGET = "QPicamera2"

Qt = QtCore.Qt
Signal = QtCore.pyqtSignal
Slot = QtCore.pyqtSlot


def preview_widget_class(software=False):
    """Return the picamera2 Qt preview widget class matching the active binding."""
    from picamera2.previews import qt as _p2qt
    return getattr(_p2qt, _SW_WIDGET if software else _GL_WIDGET)
