# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""CDAF focus grid in viewfinder corner.

8x8 PiSP focus zones on raw Bayer.
"""

from __future__ import annotations

import numpy as np

from ..qt import Qt, QtCore, QtGui, QtWidgets
from .style import GLASS_BG

_FALLBACK_FRAME = (16, 9)
_PAD = 8
_CELL_GAP = 1
# Cells span about 250:1 in one frame, 0.4 lifts the dim floor into mid gray.
_GAMMA = 0.4
_TINT = (215, 218, 224)  # histogram neutral
_ALPHA = (18, 232)
_METERED = QtGui.QColor(232, 234, 237)
_METERED_W = 2
# Bracket arm as a fraction of each side
_ARM = 0.35
_HALO = QtGui.QColor(16, 18, 22, 235)


class FocusMapOverlay(QtWidgets.QWidget):
    """8x8 focus map at frame aspect, with metered cells outlined."""

    def __init__(self, metered: int = 2, parent=None):
        super().__init__(parent)
        # Informational only: never steal viewfinder taps.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._metered = metered
        self._levels: np.ndarray | None = None
        self._card_h = 96
        self._aspect = 0.0
        self.set_frame_shape(*_FALLBACK_FRAME)

    def set_card_height(self, h: int) -> None:
        """Match histogram card height, so open corner overlays read as one row."""
        self._card_h = h
        self._resize()

    def set_frame_shape(self, width: int, height: int) -> None:
        """A cell is an eighth of frame each way, so the card carries the frame shape."""
        if width <= 0 or height <= 0:
            return
        aspect = width / height
        if abs(aspect - self._aspect) < 1e-3:
            return
        self._aspect = aspect
        self._resize()

    def _resize(self) -> None:
        cells_h = self._card_h - 2 * _PAD
        self.setFixedSize(round(cells_h * self._aspect) + 2 * _PAD, self._card_h)

    def set_levels(self, levels: np.ndarray | None) -> None:
        """Take 0..1 cells and curve them, since the span within a frame is wide."""
        self._levels = None if levels is None else np.clip(levels, 0.0, 1.0) ** _GAMMA
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
        rows, cols = levels.shape
        cw = (self.width() - 2 * _PAD) / cols
        ch = (self.height() - 2 * _PAD) / rows
        for r in range(rows):
            for c in range(cols):
                lo, hi = _ALPHA
                alpha = int(lo + (hi - lo) * float(levels[r, c]))
                p.setBrush(QtGui.QColor(*_TINT, alpha))
                p.drawRoundedRect(
                    QtCore.QRectF(_PAD + c * cw, _PAD + r * ch, cw - _CELL_GAP, ch - _CELL_GAP),
                    1.5,
                    1.5,
                )
        self._outline_metered(p, rows, cols, cw, ch)
        p.end()

    def _outline_metered(
        self, p: QtGui.QPainter, rows: int, cols: int, cw: float, ch: float
    ) -> None:
        """Mark cells the focus score comes from, so readout has a place."""
        n = self._metered
        if n <= 0 or n > min(rows, cols):
            return
        r0, c0 = (rows - n) // 2, (cols - n) // 2
        # Cells run near black to near white
        edge = _METERED_W
        x = _PAD + c0 * cw - edge
        y = _PAD + r0 * ch - edge
        w = n * cw - _CELL_GAP + 2 * edge
        h = n * ch - _CELL_GAP + 2 * edge
        # Corner brackets with gapped sides read as an ROI, not one more cell.
        ax, ay = w * _ARM, h * _ARM
        path = QtGui.QPainterPath()
        for cx, cy, sx, sy in (
            (x, y, 1, 1),
            (x + w, y, -1, 1),
            (x + w, y + h, -1, -1),
            (x, y + h, 1, -1),
        ):
            path.moveTo(cx + sx * ax, cy)
            path.lineTo(cx, cy)
            path.lineTo(cx, cy + sy * ay)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for color, width in ((_HALO, 2 * _METERED_W), (_METERED, _METERED_W)):
            p.setPen(QtGui.QPen(color, width))
            p.drawPath(path)
