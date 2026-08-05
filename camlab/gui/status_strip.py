# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Status strip: read-only live capture facts.

Centre mirrors rpicam-hello info text. Version left, board stats right.
Compact profile drops frame counter, trims stats to CPU/GPU, tap toggles card.
"""

from __future__ import annotations

from .. import __version__
from ..qt import Qt, QtCore, QtWidgets, Signal
from .rpi_stats import RpiStatsView

_GAP = 16  # inter-zone spacing


class StatusStrip(QtWidgets.QFrame):
    stats_tapped = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusStrip")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(12, 5, 12, 5)
        lay.setSpacing(_GAP)

        # Per-frame telemetry at 10 Hz, sensor temperature split off behind a hairline.
        self._tele_box = QtWidgets.QWidget(self)
        self.telemetry_lbl = QtWidgets.QLabel(self._tele_box)
        self.telemetry_lbl.setObjectName("telemetry")
        self._temp_sep = QtWidgets.QFrame(self._tele_box)
        self._temp_sep.setObjectName("vsep")
        self._temp_sep.setFixedSize(1, 11)
        self.temp_lbl = QtWidgets.QLabel(self._tele_box)
        self.temp_lbl.setObjectName("telemetry")
        trow = QtWidgets.QHBoxLayout(self._tele_box)
        trow.setContentsMargins(0, 0, 0, 0)
        trow.setSpacing(10)
        trow.addWidget(self.telemetry_lbl)
        trow.addWidget(self._temp_sep, 0, Qt.AlignmentFlag.AlignVCenter)
        trow.addWidget(self.temp_lbl)

        self.version_lbl = QtWidgets.QLabel(f"camlab v{__version__}", self)
        self.version_lbl.setObjectName("version")
        self._left = QtWidgets.QWidget(self)
        lrow = QtWidgets.QHBoxLayout(self._left)
        lrow.setContentsMargins(0, 0, 0, 0)
        lrow.setSpacing(_GAP)
        lrow.addWidget(self.version_lbl)
        lrow.addStretch(1)

        # Two renders of one 1 Hz sample: monitor fits all five fields, panel keeps CPU/GPU.
        self.stats = RpiStatsView(parent=self)
        self.stats_compact = RpiStatsView(fields=("cpu", "gpu"), parent=self)
        self._right = QtWidgets.QWidget(self)
        rrow = QtWidgets.QHBoxLayout(self._right)
        rrow.setContentsMargins(0, 0, 0, 0)
        rrow.setSpacing(_GAP)
        rrow.addStretch(1)
        rrow.addWidget(self.stats)
        rrow.addWidget(self.stats_compact)
        self._right.installEventFilter(self)

        lay.addWidget(self._left)
        lay.addStretch(1)
        lay.addWidget(self._tele_box)
        lay.addStretch(1)
        lay.addWidget(self._right)

        self._frame: int | None = None
        self._fps: float | None = None
        self._exp_us: float | None = None
        self._ag: float | None = None
        self._dg: float | None = None
        self._temp: float | None = None
        self._compact = False

        self.set_telemetry(None, None)
        self._sync_balance()

    def set_compact(self, compact: bool) -> None:
        """Shorten version, drop frame counter, trim stats.

        Version stays on screen for bug reports.
        """
        self._compact = bool(compact)
        self.version_lbl.setText(f"v{__version__}" if self._compact else f"camlab v{__version__}")
        self._right.setCursor(
            Qt.CursorShape.PointingHandCursor if self._compact else Qt.CursorShape.ArrowCursor
        )
        self._right.setToolTip("Toggles the remaining board stats." if self._compact else "")
        self._sync_stats()
        self._render_telemetry()
        self._sync_balance()

    def eventFilter(self, obj, ev) -> bool:
        # On press, not release: Qt folds a quick second tap into DblClick.
        if (
            obj is self._right
            and self._compact
            and ev.type()
            in (QtCore.QEvent.Type.MouseButtonPress, QtCore.QEvent.Type.MouseButtonDblClick)
        ):
            self.stats_tapped.emit()
            return True
        return super().eventFilter(obj, ev)

    def _active_stats(self) -> RpiStatsView:
        return self.stats_compact if self._compact else self.stats

    def _sync_stats(self) -> None:
        active = self._active_stats()
        for view in (self.stats, self.stats_compact):
            view.setVisible(view is active and view.has_data)

    def _sync_balance(self) -> None:
        """Pin both zones to wider one so centred telemetry cannot drift.

        Compact has no width to spare: zones hug content, telemetry floats.
        """
        left_min = self.version_lbl.sizeHint().width()
        active = self._active_stats()
        right_min = active.sizeHint().width() if active.has_data else 0
        if self._compact:
            self._left.setFixedWidth(left_min)
            self._right.setFixedWidth(right_min)
        else:
            width = max(left_min, right_min)
            self._left.setFixedWidth(width)
            self._right.setFixedWidth(width)

    def set_telemetry(
        self,
        frame: int | None,
        fps: float | None,
        exposure_us: float | None = None,
        analogue_gain: float | None = None,
        digital_gain: float | None = None,
    ) -> None:
        """Live per-frame numbers from engine and libcamera metadata.

        frame None hides line rather than placeholders.
        """
        self._frame = frame
        self._fps = fps
        self._exp_us = exposure_us
        self._ag = analogue_gain
        self._dg = digital_gain
        self._render_telemetry()

    def set_temperature(self, temp_c: float | None) -> None:
        """Sensor temperature (degC) when reported. None keeps last reading."""
        if temp_c is None:
            return
        self._temp = temp_c
        self._render_telemetry()

    def _render_telemetry(self) -> None:
        live = self._frame is not None
        self.telemetry_lbl.setVisible(live)
        if live:
            fps = f"{self._fps:.2f}" if self._fps is not None else "--.--"
            # Compact drops the frame counter: gains carry more per pixel.
            parts = [f"{fps} fps" if self._compact else f"#{self._frame} ({fps} fps)"]
            if self._exp_us is not None:
                parts.append(f"exp {round(self._exp_us)}")
            if self._ag is not None:
                parts.append(f"ag {self._ag:.2f}")
            if self._dg is not None:
                parts.append(f"dg {self._dg:.2f}")
            self.telemetry_lbl.setText(" ".join(parts))
        has_temp = self._temp is not None
        self._temp_sep.setVisible(has_temp and live)
        self.temp_lbl.setVisible(has_temp)
        if has_temp:
            self.temp_lbl.setText(f"{self._temp:.1f}\u00b0C")

    def set_rpi_stats(self, s) -> None:
        """Board facts (RpiStatsSample), missing sources drop out."""
        self.stats.set_stats(s)
        self.stats_compact.set_stats(s)
        self._sync_stats()
        self._sync_balance()
