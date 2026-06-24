"""Status strip - facts about the live capture, including the integrity indicator.

Per spec this surfaces facts, never a pass/fail verdict. The integrity chip shows
a running error count + a rolling rate and turns prominent when > 0.
"""

from __future__ import annotations

from ..integrity import CATEGORY_LABELS, IntegrityStats
from ..modes import format_fps
from ..qt import Qt, QtWidgets, Slot
from . import icons


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
        self.camera_lbl = _chip(self)
        self.mode_lbl = _chip(self)
        self.boot_lbl = _chip(self)

        for w in (self.camera_lbl, self.mode_lbl, self.boot_lbl):
            lay.addWidget(w)
        lay.addStretch(1)

        self.integrity_lbl = QtWidgets.QLabel(self)
        self.integrity_lbl.setObjectName("integrity")
        self.integrity_lbl.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        lay.addWidget(self.integrity_lbl)

        self.set_camera(None, None)
        self.set_mode("-", "-")
        self.set_boot_time(None)
        self.update_integrity(IntegrityStats())

    def set_camera(self, detected_model: str | None, selected_overlay: str | None) -> None:
        """Show the enumerated model + whether it matches the selected overlay."""
        if not detected_model:
            ic = icons.html("error", color="#e06c75")
            self.camera_lbl.setText(
                f"{ic} Camera: <span style='color:#e06c75'>not detected</span>")
            return
        if selected_overlay and detected_model.lower() == selected_overlay.lower():
            ic = icons.html("check_circle", color="#98c379")
            self.camera_lbl.setText(f"{ic} Camera: {detected_model}")
        elif selected_overlay:
            ic = icons.html("warning", color="#e5c07b")
            self.camera_lbl.setText(
                f"{ic} Camera: {detected_model} "
                f"<span style='color:#e5c07b'>(sel: {selected_overlay})</span>")
        else:
            ic = icons.html("photo_camera", color="#aeb4bf")
            self.camera_lbl.setText(f"{ic} Camera: {detected_model}")

    def set_mode(self, fmt: str, size: str, fps: float | None = None) -> None:
        text = f"Mode: {fmt} {size}"
        if fps is not None:
            text += f" @ {format_fps(fps)} fps"
        self.mode_lbl.setText(text)

    def set_boot_time(self, seconds: float | None) -> None:
        value = f"{seconds:.1f}s" if seconds is not None else "..."
        self.boot_lbl.setText(f"boot time: {value}")

    @Slot(object)
    def update_integrity(self, stats: IntegrityStats) -> None:
        # The icon span carries no color so it inherits the label's state color
        # (see the QLabel#integrity[state=...] rules in the main stylesheet).
        if stats.healthy:
            ic = icons.html("check_circle")
            self.integrity_lbl.setText(f"{ic} INTEGRITY: clean")
            self.integrity_lbl.setProperty("state", "ok")
            self.integrity_lbl.setToolTip("No camera-stack integrity errors observed.")
        else:
            name = "warning" if stats.rate_hz == 0 else "error"
            ic = icons.html(name)
            self.integrity_lbl.setText(
                f"{ic} INTEGRITY: {stats.total} errs ({stats.rate_hz:.0f}/s)")
            self.integrity_lbl.setProperty("state", "warn" if stats.rate_hz == 0 else "bad")
            self.integrity_lbl.setToolTip(self._breakdown(stats))
        # re-polish to apply the [state] property selector
        self.integrity_lbl.style().unpolish(self.integrity_lbl)
        self.integrity_lbl.style().polish(self.integrity_lbl)

    @staticmethod
    def _breakdown(stats: IntegrityStats) -> str:
        lines = ["Camera-stack integrity errors (facts, not a verdict):"]
        for cat, n in sorted(stats.by_category.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {CATEGORY_LABELS.get(cat, cat)}: {n}")
        return "\n".join(lines)
