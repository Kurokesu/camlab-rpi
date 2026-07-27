# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Views for an RpiStatsSample (CPU, GPU, RAM, SoC and RP1 temperatures).

RpiStatsView renders whichever fields it is given as a row: the status strip
shows all five on a monitor and CPU/GPU on the touch panel. RpiStatsCard is
the vertical glass overlay the panel opens for the remaining fields.
"""

from __future__ import annotations

from ..qt import Qt, QtCore, QtGui, QtWidgets
from .style import GLASS_BG

FIELDS = ("cpu", "gpu", "ram", "soc", "rp1")
# Complement of the CPU/GPU pair the compact strip keeps on screen.
CARD_FIELDS = ("ram", "soc", "rp1")


def pad(text: str, width: int) -> str:
    """Pad with trailing figure spaces (digit-width) to `width` chars.

    Keeps the field's width constant across digit-count changes while the
    value itself hugs its label. The slack lands before the next separator
    where it is invisible."""
    return text.ljust(width, "\u2007")


def _pct(value: float | None) -> str | None:
    """Percentage capped at 99, so field never widens to three digits."""
    return None if value is None else pad(f"{min(value, 99):.0f}%", 3)


def _texts(s) -> dict[str, str | None]:
    """Rendered text per field (None when source is missing)."""
    ram = None
    if s.ram_used_mb is not None and s.ram_total_mb is not None:
        total = f"{s.ram_total_mb / 1024:.1f}"
        used = pad(f"{s.ram_used_mb / 1024:.1f}", len(total))
        ram = f"RAM {used}/{total}GB"
    cpu, gpu = _pct(s.cpu_pct), _pct(s.gpu_pct)
    return {
        "cpu": f"CPU {cpu}" if cpu is not None else None,
        "gpu": f"GPU {gpu}" if gpu is not None else None,
        "ram": ram,
        "soc": f"SoC {s.soc_temp_c:.0f}\u00b0C" if s.soc_temp_c is not None else None,
        "rp1": f"RP1 {s.rp1_temp_c:.0f}\u00b0C" if s.rp1_temp_c is not None else None,
    }


class RpiStatsView(QtWidgets.QWidget):
    """Row of board facts split by hairlines, refreshed at 1 Hz."""

    def __init__(self, fields: tuple[str, ...] = FIELDS, parent=None):
        super().__init__(parent)
        self._fields = fields
        self._labels: dict[str, QtWidgets.QLabel] = {}
        self._seps: dict[str, QtWidgets.QFrame] = {}
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        for i, field in enumerate(fields):
            if i:
                sep = QtWidgets.QFrame(self)
                sep.setObjectName("vsep")
                sep.setFixedSize(1, 11)
                row.addWidget(sep, 0, Qt.AlignmentFlag.AlignVCenter)
                self._seps[field] = sep
            lbl = QtWidgets.QLabel(self)
            lbl.setObjectName("rpiStats")
            row.addWidget(lbl)
            self._labels[field] = lbl
        self._has_data = False
        self.setVisible(False)

    @property
    def has_data(self) -> bool:
        """True once at least one field rendered, so callers can hide an
        empty cluster instead of an empty gap."""
        return self._has_data

    def set_stats(self, s) -> bool:
        """Render an RpiStatsSample. Fields with no source drop out along
        with their leading hairline. Returns has_data."""
        texts = _texts(s)
        shown_any = False
        for field in self._fields:
            text = texts.get(field)
            visible = text is not None
            lbl = self._labels[field]
            lbl.setVisible(visible)
            if visible:
                lbl.setText(text)
            sep = self._seps.get(field)
            if sep is not None:
                sep.setVisible(visible and shown_any)
            shown_any = shown_any or visible
        self._has_data = shown_any
        return shown_any


class RpiStatsCard(QtWidgets.QWidget):
    """Fields the compact strip has no room for, one per line on glass.

    Overlays viewfinder on touch panel, where strip only fits
    CPU and GPU. Same glass as histogram overlay card."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Purely informational: never steal taps from the viewfinder.
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        col = QtWidgets.QVBoxLayout(self)
        col.setContentsMargins(14, 10, 14, 10)
        col.setSpacing(4)
        self._labels: dict[str, QtWidgets.QLabel] = {}
        for field in CARD_FIELDS:
            lbl = QtWidgets.QLabel(self)
            lbl.setObjectName("statsCard")
            col.addWidget(lbl)
            self._labels[field] = lbl

    def set_stats(self, s) -> None:
        """Render an RpiStatsSample, fields with no source drop out."""
        texts = _texts(s)
        for field, lbl in self._labels.items():
            text = texts[field]
            lbl.setVisible(text is not None)
            if text is not None:
                lbl.setText(text)

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(GLASS_BG)
        p.drawRoundedRect(QtCore.QRectF(self.rect()), 8, 8)
        p.end()
