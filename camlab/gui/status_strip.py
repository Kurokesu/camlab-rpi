"""Status strip - the top bar of live, read-only facts about the capture.

Everything here is a FACT, never a pass/fail verdict (per spec). The left side
mirrors rpicam-hello's default info-text (#frame (fps fps) exp ag dg) plus the
sensor temperature. The right side carries the boot-to-preview time and two
independent counters (errors / warnings) that stay green while clear and turn
red / orange the moment either climbs. Rendered as flat text (no chip boxes) so
it reads as read-only, visually distinct from the clickable controls below.
"""

from __future__ import annotations

from .. import __version__
from ..integrity import CATEGORY_LABELS, CATEGORY_SEVERITY, IntegrityStats
from ..qt import QtWidgets, Slot


class StatusStrip(QtWidgets.QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusStrip")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(12, 5, 12, 5)
        lay.setSpacing(16)

        # Live per-frame telemetry, one rpicam-style string refreshed at 10 Hz.
        self.telemetry_lbl = QtWidgets.QLabel(self)
        self.telemetry_lbl.setObjectName("telemetry")
        # One-shot boot-to-preview fact, plus the two integrity counters.
        self.boot_lbl = QtWidgets.QLabel(self)
        self.boot_lbl.setObjectName("bootInfo")
        self.errors_lbl = QtWidgets.QLabel(self)
        self.errors_lbl.setObjectName("errCount")
        self.warnings_lbl = QtWidgets.QLabel(self)
        self.warnings_lbl.setObjectName("warnCount")

        # boot + counters anchor right. The telemetry is centred by balancing it
        # against a left zone kept as wide as the right cluster, so it sits at the
        # true centre rather than the midpoint of the leftover space.
        self._right = QtWidgets.QWidget(self)
        rrow = QtWidgets.QHBoxLayout(self._right)
        rrow.setContentsMargins(0, 0, 0, 0)
        rrow.setSpacing(16)
        rrow.addWidget(self.boot_lbl)
        rrow.addWidget(self.errors_lbl)
        rrow.addWidget(self.warnings_lbl)

        # Build version on the left so the operator can read the running build at a
        # glance. It lives in the balanced left zone, so telemetry stays centred.
        self.version_lbl = QtWidgets.QLabel(f"camlab v{__version__}", self)
        self.version_lbl.setObjectName("version")
        self._left = QtWidgets.QWidget(self)
        lrow = QtWidgets.QHBoxLayout(self._left)
        lrow.setContentsMargins(0, 0, 0, 0)
        lrow.setSpacing(16)
        lrow.addWidget(self.version_lbl)
        lrow.addStretch(1)

        lay.addWidget(self._left)
        lay.addStretch(1)
        lay.addWidget(self.telemetry_lbl)
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
        """Keep telemetry centred: the left zone matches the right cluster's width,
        but never narrower than the version text it holds (so it cannot clip)."""
        width = max(self._right.sizeHint().width(),
                    self.version_lbl.sizeHint().width())
        self._left.setFixedWidth(width)

    def set_telemetry(self, frame: int | None, fps: float | None,
                      exposure_us: float | None = None,
                      analogue_gain: float | None = None,
                      digital_gain: float | None = None) -> None:
        """Live per-frame numbers from the engine + libcamera metadata."""
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
        frame = self._frame if self._frame is not None else 0
        fps = f"{self._fps:.2f}" if self._fps is not None else "--.--"
        parts = [f"#{frame} ({fps} fps)"]
        if self._exp_us is not None:
            parts.append(f"exp {int(round(self._exp_us))}")
        if self._ag is not None:
            parts.append(f"ag {self._ag:.2f}")
        if self._dg is not None:
            parts.append(f"dg {self._dg:.2f}")
        text = " ".join(parts)
        if self._temp is not None:
            text += f" \u00b7 {self._temp:.1f}\u00b0C"
        self.telemetry_lbl.setText(text)

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
        label.style().unpolish(label)
        label.style().polish(label)

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
