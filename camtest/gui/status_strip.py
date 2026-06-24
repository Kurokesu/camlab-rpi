"""Status strip - facts about the live capture, including the integrity indicator.

Per spec this surfaces facts, never a pass/fail verdict. The integrity chip shows
a running error count + a rolling rate and turns prominent when > 0.
"""

from __future__ import annotations

from ..integrity import CATEGORY_LABELS, IntegrityStats
from ..qt import QtWidgets, Slot
from . import icons
from .widgets import IconChip


def _chip(parent=None) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(parent)
    lbl.setProperty("class", "chip")
    return lbl


class StatusStrip(QtWidgets.QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusStrip")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(14)

        # The selected sensor + port lives on the toolbar button (the control you
        # click to change it). The strip only carries live, complementary facts:
        # what libcamera actually enumerated, the mode, and boot-to-preview.
        self.camera_lbl = IconChip(self)        # icon + text -> composite chip
        self.mode_lbl = _chip(self)
        # fps + exposure + gains are one cluster: all live per-frame telemetry
        # refreshed together at 10 Hz (matches rpicam-apps' info-text grouping).
        self.telemetry_lbl = _chip(self)
        self.boot_lbl = _chip(self)

        for w in (self.camera_lbl, self.mode_lbl, self.telemetry_lbl,
                  self.boot_lbl):
            lay.addWidget(w)
        lay.addStretch(1)

        self._fps: float | None = None
        self._exp_us: float | None = None
        self._ag: float | None = None
        self._dg: float | None = None
        self._temp: float | None = None

        self.integrity_lbl = IconChip(self, object_name="integrity", chip_class="")
        lay.addWidget(self.integrity_lbl)

        self.set_camera(None, None)
        self.set_mode("-", "-")
        self.set_fps(None)
        self.set_exposure(None)
        self.set_boot_time(None)
        self.update_integrity(IntegrityStats())

    def _camera_icon(self, name: str, color: str):
        return icons.pixmap(name, self.camera_lbl.icon_size(), color)

    def set_camera(self, detected_model: str | None, selected_overlay: str | None) -> None:
        """Show the enumerated model + whether it matches the selected overlay."""
        if not detected_model:
            self.camera_lbl.set_content(
                self._camera_icon("error", "#e06c75"),
                "Camera: <span style='color:#e06c75'>not detected</span>")
        elif selected_overlay and detected_model.lower() == selected_overlay.lower():
            self.camera_lbl.set_content(
                self._camera_icon("check_circle", "#98c379"),
                f"Camera: {detected_model}")
        elif selected_overlay:
            self.camera_lbl.set_content(
                self._camera_icon("warning", "#e5c07b"),
                f"Camera: {detected_model} "
                f"<span style='color:#e5c07b'>(sel: {selected_overlay})</span>")
        else:
            self.camera_lbl.set_content(
                self._camera_icon("photo_camera", "#aeb4bf"),
                f"Camera: {detected_model}")

    def set_mode(self, fmt: str, size: str) -> None:
        self.mode_lbl.setText(f"Mode: {fmt} {size}")

    def set_fps(self, fps: float | None) -> None:
        """Instantaneous frame rate (rpicam-style), sampled live by MainWindow."""
        self._fps = fps
        self._render_telemetry()

    def set_exposure(self, exposure_us: float | None = None,
                     analogue_gain: float | None = None,
                     digital_gain: float | None = None) -> None:
        """Actual per-frame exposure + gains from the libcamera metadata."""
        self._exp_us = exposure_us
        self._ag = analogue_gain
        self._dg = digital_gain
        self._render_telemetry()

    def set_temperature(self, temp_c: float | None) -> None:
        """Sensor temperature (degC), if the sensor reports it.

        Sticky: a None (sensor doesn't offer it, or a frame's embedded data
        failed to parse) keeps the last reading instead of dropping the chip.
        """
        if temp_c is None:
            return
        self._temp = temp_c
        self._render_telemetry()

    def _render_telemetry(self) -> None:
        """One chip for the live per-frame numbers: FPS | Exp | AG | DG | Temp."""
        parts = [f"FPS {self._fps:.1f}" if self._fps is not None else "FPS --"]
        if self._exp_us is not None:
            parts.append(f"Exp {self._exp_us / 1000.0:.2f} ms")
            if self._ag is not None:
                parts.append(f"AG {self._ag:.2f}")
            if self._dg is not None:
                parts.append(f"DG {self._dg:.2f}")
        else:
            parts.append("Exp --")
        if self._temp is not None:
            parts.append(f"Temp {self._temp:.1f}\u00b0C")
        self.telemetry_lbl.setText(" | ".join(parts))

    def set_boot_time(self, seconds: float | None) -> None:
        value = f"{seconds:.1f}s" if seconds is not None else "..."
        self.boot_lbl.setText(f"boot time: {value}")

    @Slot(object)
    def update_integrity(self, stats: IntegrityStats) -> None:
        # The icon is a pixmap, so its color is baked in rather than inherited:
        # keep it matched to the QFrame#integrity[state=...] text color.
        size = self.integrity_lbl.icon_size()
        if stats.healthy:
            pm = icons.pixmap("check_circle", size, "#98c379")
            self.integrity_lbl.set_content(pm, "INTEGRITY: clean")
            self.integrity_lbl.set_state("ok")
            self.integrity_lbl.setToolTip("No camera-stack integrity errors observed.")
        else:
            warn = stats.rate_hz == 0
            color = "#e5c07b" if warn else "#ffffff"
            pm = icons.pixmap("warning", size, color)
            self.integrity_lbl.set_content(
                pm, f"INTEGRITY: {stats.total} errs ({stats.rate_hz:.0f}/s)")
            self.integrity_lbl.set_state("warn" if warn else "bad")
            self.integrity_lbl.setToolTip(self._breakdown(stats))

    @staticmethod
    def _breakdown(stats: IntegrityStats) -> str:
        lines = ["Camera-stack integrity errors (facts, not a verdict):"]
        for cat, n in sorted(stats.by_category.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {CATEGORY_LABELS.get(cat, cat)}: {n}")
        return "\n".join(lines)
