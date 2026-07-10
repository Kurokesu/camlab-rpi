"""Single chokepoint for Qt imports. The whole app imports Qt from here."""

from PyQt6 import QtCore, QtGui, QtWidgets  # noqa: F401
from PyQt6.QtOpenGLWidgets import QOpenGLWidget  # noqa: F401

Qt = QtCore.Qt
Signal = QtCore.pyqtSignal
Slot = QtCore.pyqtSlot
