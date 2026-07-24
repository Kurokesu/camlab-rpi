# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Status strip - the top bar of live, read-only facts about the capture.

Everything here is a FACT, never a pass/fail verdict (per spec). The left side
mirrors rpicam-hello's default info-text (#frame (fps fps) exp ag dg) plus the
sensor temperature. The right side carries the boot-to-viewfinder time and two
independent counters (errors / warnings) that stay green while clear and turn
red / orange the moment either climbs. Rendered as flat text (no chip boxes) so
it reads as read-only, visually distinct from the clickable controls below.
"""

from __future__ import annotations

from .. import __version__
from ..integrity import CATEGORY_LABELS, CATEGORY_SEVERITY, IntegrityStats
from ..qt import Qt, QtWidgets, Slot
from .widgets import repolish


def _pad(text: str, width: int) -> str:
    """Pad with trailing figure spaces (digit-width) to `width` chars.

    Keeps the field's width constant across digit-count changes while the
    value itself hugs its label. The slack lands before the next separator
    where it is invisible."""
    return text.ljust(width, "\u2007")


class StatusStrip(QtWidgets.QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusStrip")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(12, 5, 12, 5)
        lay.setSpacing(16)

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
        # One-shot boot-to-viewfinder fact, plus the two integrity counters.
        self.boot_lbl = QtWidgets.QLabel(self)
        self.boot_lbl.setObjectName("bootInfo")
        self.errors_lbl = QtWidgets.QLabel(self)
        self.errors_lbl.setObjectName("errCount")
        self.warnings_lbl = QtWidgets.QLabel(self)
        self.warnings_lbl.setObjectName("warnCount")

        # boot + counters anchor right. The telemetry is centred by fixing the
        # left and right zones to one shared width (the wider one's content),
        # so the stretches around it always split the leftover space evenly.
        self._right = QtWidgets.QWidget(self)
        rrow = QtWidgets.QHBoxLayout(self._right)
        rrow.setContentsMargins(0, 0, 0, 0)
        rrow.setSpacing(16)
        rrow.addStretch(1)  # packs the cluster right when the zone is wider
        rrow.addWidget(self.boot_lbl)
        rrow.addWidget(self.errors_lbl)
        rrow.addWidget(self.warnings_lbl)

        # Build version on the left so the operator can read the running build at a
        # glance, followed by board health facts (sampled at 1 Hz by MainWindow).
        # Both live in the balanced left zone, so telemetry stays centred.
        self.version_lbl = QtWidgets.QLabel(f"camlab v{__version__}", self)
        self.version_lbl.setObjectName("version")
        # One label per health fact, split by hairlines. Fields whose source
        # is missing stay hidden along with their leading hairline.
        self._rpi_box = QtWidgets.QWidget(self)
        self._rpi_fields: list[QtWidgets.QLabel] = []
        self._rpi_seps: list[QtWidgets.QFrame] = []
        rrpi = QtWidgets.QHBoxLayout(self._rpi_box)
        rrpi.setContentsMargins(0, 0, 0, 0)
        rrpi.setSpacing(10)
        for i in range(5):  # CPU, GPU, RAM, SoC, RP1
            if i:
                sep = QtWidgets.QFrame(self._rpi_box)
                sep.setObjectName("vsep")
                sep.setFixedSize(1, 11)
                rrpi.addWidget(sep, 0, Qt.AlignmentFlag.AlignVCenter)
                self._rpi_seps.append(sep)
            lbl = QtWidgets.QLabel(self._rpi_box)
            lbl.setObjectName("rpiStats")
            rrpi.addWidget(lbl)
            self._rpi_fields.append(lbl)
        self._rpi_shown = False
        self._rpi_box.setVisible(False)
        self._left = QtWidgets.QWidget(self)
        lrow = QtWidgets.QHBoxLayout(self._left)
        lrow.setContentsMargins(0, 0, 0, 0)
        lrow.setSpacing(16)
        lrow.addWidget(self.version_lbl)
        lrow.addWidget(self._rpi_box)
        lrow.addStretch(1)

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

        self.set_telemetry(None, None)
        self.set_boot_time(None)
        self.update_integrity(IntegrityStats())
        self._sync_balance()

    def _sync_balance(self) -> None:
        """Give the left and right zones one shared fixed width (the wider
        one's content), so the centred telemetry cannot drift sideways."""
        left_min = self.version_lbl.sizeHint().width()
        if self._rpi_shown:
            left_min += 16 + self._rpi_box.sizeHint().width()
        right_min = sum(w.sizeHint().width() for w in
                        (self.boot_lbl, self.errors_lbl, self.warnings_lbl))
        right_min += 2 * 16  # inter-label spacing
        width = max(left_min, right_min)
        self._left.setFixedWidth(width)
        self._right.setFixedWidth(width)

    def set_telemetry(self, frame: int | None, fps: float | None,
                      exposure_us: float | None = None,
                      analogue_gain: float | None = None,
                      digital_gain: float | None = None) -> None:
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
            parts = [f"#{self._frame} ({fps} fps)"]
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
        """Board health facts (RpiStatsSample), missing sources drop out.

        Numbers are figure-space padded to their widest realistic form, so a
        digit-count change cannot shift the fields after it."""
        ram = None
        if s.ram_used_mb is not None and s.ram_total_mb is not None:
            total = f"{s.ram_total_mb / 1024:.1f}"
            used = _pad(f"{s.ram_used_mb / 1024:.1f}", len(total))
            ram = f"RAM {used}/{total}GB"
        texts = [
            f"CPU {_pad(f'{s.cpu_pct:.0f}%', 4)}" if s.cpu_pct is not None else None,
            f"GPU {_pad(f'{s.gpu_pct:.0f}%', 4)}" if s.gpu_pct is not None else None,
            ram,
            f"SoC {s.soc_temp_c:.0f}\u00b0C" if s.soc_temp_c is not None else None,
            f"RP1 {s.rp1_temp_c:.0f}\u00b0C" if s.rp1_temp_c is not None else None,
        ]
        shown_any = False
        for i, (lbl, text) in enumerate(zip(self._rpi_fields, texts)):
            visible = text is not None
            lbl.setVisible(visible)
            if visible:
                lbl.setText(text)
            if i:
                self._rpi_seps[i - 1].setVisible(visible and shown_any)
            shown_any = shown_any or visible
        self._rpi_shown = shown_any
        self._rpi_box.setVisible(shown_any)
        self._sync_balance()

    def set_boot_time(self, seconds: float | None) -> None:
        value = f"{seconds:.1f}s" if seconds is not None else "..."
        self.boot_lbl.setText(f"boot time {value}")
        self._sync_balance()

    @Slot(object)
    def update_integrity(self, stats: IntegrityStats) -> None:
        self._set_counter(self.errors_lbl, stats.errors, "errors")
        self._set_counter(self.warnings_lbl, stats.warnings, "warnings")
        self.errors_lbl.setToolTip(self._breakdown(stats, "error"))
        self.warnings_lbl.setToolTip(self._breakdown(stats, "warning"))
        self._sync_balance()

    @staticmethod
    def _set_counter(label: QtWidgets.QLabel, count: int, noun: str) -> None:
        label.setText(f"{count} {noun}")
        label.setProperty("sev", "alert" if count > 0 else "ok")
        repolish(label)

    @staticmethod
    def _breakdown(stats: IntegrityStats, severity: str) -> str:
        noun = "errors" if severity == "error" else "warnings"
        rows = [
            f"  {CATEGORY_LABELS.get(cat, cat)}: {n}"
            for cat, n in sorted(stats.by_category.items(), key=lambda kv: -kv[1])
            if n and CATEGORY_SEVERITY.get(cat) == severity
        ]
        if not rows:
            return f"No camera-stack {noun} observed."
        return f"Camera-stack {noun} (facts, not a verdict):\n" + "\n".join(rows)
