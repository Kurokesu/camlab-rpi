"""HistogramOverlay - live luma histogram in the viewfinder's corner.

Data comes from the PiSP frontend's AGC statistics (CameraEngine.agc_histogram),
so drawing is the only cost here. Painted as a translucent glass card matching
the sheets/modals, with the 1024 ISP bins folded to one column per plot pixel
and square-root scaled so shadow detail stays visible next to dominant peaks.
"""

from __future__ import annotations

import numpy as np

from ..qt import Qt, QtCore, QtGui, QtWidgets
from .control_sheet import GLASS_BG

MARGIN = 12           # from the viewfinder area's top-left corner
_SIZE = (256, 96)     # card size, plot fills it minus padding
_PAD = 8
_CURVE = QtGui.QColor(215, 218, 224, 230)
_FILL = QtGui.QColor(215, 218, 224, 90)


class HistogramOverlay(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(*_SIZE)
        # Purely informational: never steal clicks from the viewfinder.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._levels: np.ndarray | None = None

    def set_histogram(self, bins: np.ndarray) -> None:
        """Fold the 1024 ISP bins to plot columns and cache 0..1 levels."""
        cols = _SIZE[0] - 2 * _PAD
        group = len(bins) // cols
        folded = bins[:group * cols].reshape(cols, group).sum(axis=1)
        peak = folded.max()
        if peak == 0:
            self._levels = None
        else:
            self._levels = np.sqrt(folded / peak)
        self.update()

    def clear(self) -> None:
        self._levels = None
        self.update()

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(GLASS_BG)
        p.drawRoundedRect(QtCore.QRectF(self.rect()), 8, 8)
        levels = self._levels
        if levels is None:
            p.end()
            return
        base = self.height() - _PAD
        span = self.height() - 2 * _PAD
        path = QtGui.QPainterPath(QtCore.QPointF(_PAD, base))
        for i, level in enumerate(levels):
            path.lineTo(_PAD + i, base - float(level) * span)
        path.lineTo(_PAD + len(levels) - 1, base)
        path.closeSubpath()
        p.setPen(QtGui.QPen(_CURVE, 1))
        p.setBrush(_FILL)
        p.drawPath(path)
        p.end()
