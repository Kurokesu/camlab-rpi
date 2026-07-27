# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Status strip - the top bar of live, read-only facts about the capture.

Everything here is a FACT, never a pass/fail verdict (per spec). The centre
mirrors rpicam-hello's default info-text (#frame (fps fps) exp ag dg) plus the
sensor temperature. The build version anchors the left, next to the board
stats on a monitor. The touch panel drops the frame counter for width and pins
SoC and RP1 on the right, so throttling is visible without opening any panel.
Rendered as flat text (no chip boxes) so it reads as read-only, visually
distinct from the clickable controls below.
"""

from __future__ import annotations

from .. import __version__
from ..qt import Qt, QtWidgets
from .rpi_stats import RpiStatsView

# Inter-zone spacing, also the gap _sync_balance accounts for.
_GAP = 16


class StatusStrip(QtWidgets.QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusStrip")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(12, 5, 12, 5)
        lay.setSpacing(_GAP)

        # Live per-frame telemetry, one rpicam-style string refreshed at 10 Hz,
        # with the sensor temperature split off behind a hairline.
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

        # Build version on the left so the operator can read the running build
        # at a glance, followed by board stats (sampled at 1 Hz by MainWindow).
        self.version_lbl = QtWidgets.QLabel(f"camlab v{__version__}", self)
        self.version_lbl.setObjectName("version")
        self.rpi = RpiStatsView(parent=self)
        self._left = QtWidgets.QWidget(self)
        lrow = QtWidgets.QHBoxLayout(self._left)
        lrow.setContentsMargins(0, 0, 0, 0)
        lrow.setSpacing(_GAP)
        lrow.addWidget(self.version_lbl)
        lrow.addWidget(self.rpi)
        lrow.addStretch(1)

        # Temperatures anchor right on the panel, where the full cluster does
        # not fit. Telemetry is centred by fixing left and right zones to one
        # shared width (the wider one's content), so the stretches around it
        # always split the leftover space evenly.
        self.temps = RpiStatsView(fields=("soc", "rp1"), parent=self)
        self._right = QtWidgets.QWidget(self)
        rrow = QtWidgets.QHBoxLayout(self._right)
        rrow.setContentsMargins(0, 0, 0, 0)
        rrow.setSpacing(_GAP)
        rrow.addStretch(1)
        rrow.addWidget(self.temps)

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
        """Compact shortens the version and splits board stats: temperatures
        move right, load and RAM go to the log panel. The build stays on
        screen, RELEASING.md and the bug report template both point here."""
        self._compact = bool(compact)
        self.version_lbl.setText(f"v{__version__}" if self._compact else f"camlab v{__version__}")
        self._sync_rpi()
        self._render_telemetry()
        self._sync_balance()

    def _sync_rpi(self) -> None:
        self.rpi.setVisible(self.rpi.has_data and not self._compact)
        self.temps.setVisible(self.temps.has_data and self._compact)

    def _sync_balance(self) -> None:
        """Give the left and right zones one shared fixed width (the wider
        one's content), so the centred telemetry cannot drift sideways."""
        left_min = self.version_lbl.sizeHint().width()
        if self.rpi.isVisible():
            left_min += _GAP + self.rpi.sizeHint().width()
        right_min = self.temps.sizeHint().width() if self.temps.isVisible() else 0
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
        """Live per-frame numbers from the engine + libcamera metadata.

        frame None means no frame has been captured yet, which hides the
        whole line rather than rendering placeholders.
        """
        self._frame = frame
        self._fps = fps
        self._exp_us = exposure_us
        self._ag = analogue_gain
        self._dg = digital_gain
        self._render_telemetry()

    def set_temperature(self, temp_c: float | None) -> None:
        """Sensor temperature (degC), if the sensor reports it.

        Sticky: a None (sensor doesn't offer it, or a frame's embedded data
        failed to parse) keeps the last reading instead of dropping it.
        """
        if temp_c is None:
            return
        self._temp = temp_c
        self._render_telemetry()

    def _render_telemetry(self) -> None:
        live = self._frame is not None
        self.telemetry_lbl.setVisible(live)
        if live:
            fps = f"{self._fps:.2f}" if self._fps is not None else "--.--"
            # Compact drops the frame counter: it is a liveness cue rather
            # than a number to read, and the gains carry more per pixel.
            # Parens go with it, they only ever bracketed the counter.
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
        self.rpi.set_stats(s)
        self.temps.set_stats(s)
        self._sync_rpi()
        self._sync_balance()
