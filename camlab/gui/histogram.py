# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Live luma histogram in viewfinder corner.

Data from PiSP AGC stats. Draw-only cost. Glass card matches sheets. 1024 ISP
bins fold to one column per plot pixel, square-root scaled so shadows stay
visible next to peaks.
"""

from __future__ import annotations

import numpy as np

from ..qt import Qt, QtCore, QtGui, QtWidgets
from .style import GLASS_BG

MARGIN = 12  # from viewfinder top-left
_ASPECT = 8 / 3  # card w:h
_PAD = 8
_CURVE = QtGui.QColor(215, 218, 224, 230)
_FILL = QtGui.QColor(215, 218, 224, 90)


class HistogramOverlay(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        # Informational only: never steal viewfinder taps.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._levels: np.ndarray | None = None
        self.set_card_height(96)

    def set_card_height(self, h: int) -> None:
        """Profile sets height, width follows the aspect."""
        self.setFixedSize(round(h * _ASPECT), h)
        self._levels = None

    def set_histogram(self, bins: np.ndarray) -> None:
        """Fold 1024 ISP bins to plot columns and cache 0..1 levels."""
        cols = self.width() - 2 * _PAD
        group = len(bins) // cols
        folded = bins[: group * cols].reshape(cols, group).sum(axis=1)
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
