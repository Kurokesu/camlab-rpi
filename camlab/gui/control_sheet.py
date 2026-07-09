"""ControlSheet - floating auto/manual + slider row for one camera control.

Floats over preview's bottom edge as a native subsurface, so the picture stays
live and full-size while tuning (a plain Qt-painted widget cannot stack above
the native GL preview). Opaque by necessity: translucent subsurfaces do not
composite under Qt5's Wayland backend (verified on device). In auto the slider
silently tracks live metadata value, touching it flips the control to manual
at that position (no value jump). Exposure and gain span orders of magnitude,
so their sliders map logarithmically.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from ..qt import Qt, QtWidgets, Signal
from .widgets import SegmentedSelector

# Slider resolution. 1000 steps is finer than any bench display is wide.
_STEPS = 1000


def fmt_exposure(us: float) -> str:
    if us >= 10000:
        return f"{us / 1000:.1f} ms"
    if us >= 1000:
        return f"{us / 1000:.2f} ms"
    return f"{int(round(us))} \u00b5s"


def fmt_gain(gain: float) -> str:
    return f"{gain:.2f}x"


def fmt_ct(kelvin: float) -> str:
    return f"{int(round(kelvin))} K"


class JumpSlider(QtWidgets.QSlider):
    """QSlider whose groove click jumps the handle straight to that spot."""

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.setValue(QtWidgets.QStyle.sliderValueFromPosition(
                self.minimum(), self.maximum(), event.pos().x(), self.width()))
        super().mousePressEvent(event)


class ControlSheet(QtWidgets.QFrame):
    """[Auto|Manual] selector + slider + value label for one control."""

    changed = Signal(object)  # None = auto, else the manual value

    def __init__(self, title: str, fmt: Callable[[float], str],
                 log_scale: bool = False, integer: bool = True, parent=None):
        super().__init__(parent)
        self.setObjectName("controlSheet")
        # Own native (Wayland sub)surface, created after the GL preview's, so
        # it stacks above it. No WA_TranslucentBackground: an alpha subsurface
        # never composites on this stack, so the buffer must stay opaque.
        self.setAttribute(Qt.WA_NativeWindow)
        self._fmt = fmt
        self._log = log_scale
        self._int = integer
        self._lo = 1.0
        self._hi = 2.0
        self._tracking = False  # programmatic slider move, not a user action

        name = QtWidgets.QLabel(title)
        name.setObjectName("sheetTitle")
        name.setMinimumWidth(110)

        self.mode_sel = SegmentedSelector()
        self.mode_sel.set_options([("Auto", "auto"), ("Manual", "manual")],
                                  current="auto", stretch=False)
        self.mode_sel.changed.connect(self._on_mode)

        self.slider = JumpSlider(Qt.Horizontal)
        self.slider.setRange(0, _STEPS)
        self.slider.setFocusPolicy(Qt.TabFocus)
        self.slider.setCursor(Qt.PointingHandCursor)
        self.slider.valueChanged.connect(self._on_slider)
        self.slider.sliderPressed.connect(self._flip_to_manual)

        self.value_lbl = QtWidgets.QLabel("--")
        self.value_lbl.setObjectName("sheetValue")
        # Fixed width so the row does not jitter as digits change.
        self.value_lbl.setMinimumWidth(80)
        self.value_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(14, 8, 14, 8)
        row.setSpacing(14)
        row.addWidget(name)
        row.addWidget(self.mode_sel)
        row.addWidget(self.slider, 1)
        row.addWidget(self.value_lbl)

    @property
    def is_auto(self) -> bool:
        return self.mode_sel.current_value() == "auto"

    def set_range(self, lo: float, hi: float) -> None:
        self._lo = float(lo)
        self._hi = max(float(hi), self._lo + 1e-9)

    def set_state(self, value) -> None:
        """Silently seed auto (None) or manual at `value`."""
        if value is None:
            self.mode_sel.set_value("auto")
        else:
            self.mode_sel.set_value("manual")
            self._track(value)
        self._style_slider()

    def set_live(self, value) -> None:
        """Live metadata at 10 Hz. In auto, slider and label follow it."""
        if value is not None and self.is_auto:
            self._track(value)

    def _track(self, value) -> None:
        self._tracking = True
        try:
            self.slider.setValue(self._to_pos(value))
        finally:
            self._tracking = False
        self.value_lbl.setText(self._fmt(value))

    def _value(self):
        v = self._from_pos(self.slider.value())
        return int(round(v)) if self._int else v

    def _to_pos(self, value) -> int:
        v = min(max(float(value), self._lo), self._hi)
        if self._log:
            t = math.log(v / self._lo) / math.log(self._hi / self._lo)
        else:
            t = (v - self._lo) / (self._hi - self._lo)
        return int(round(t * _STEPS))

    def _from_pos(self, pos: int) -> float:
        t = pos / _STEPS
        if self._log:
            return self._lo * (self._hi / self._lo) ** t
        return self._lo + t * (self._hi - self._lo)

    def _on_mode(self) -> None:
        self._style_slider()
        self.changed.emit(None if self.is_auto else self._value())

    def _on_slider(self, _pos: int) -> None:
        if self._tracking:
            return
        # Any user move (drag, groove click, arrow key) implies manual.
        if self.is_auto:
            self.mode_sel.set_value("manual")
            self._style_slider()
        v = self._value()
        self.value_lbl.setText(self._fmt(v))
        self.changed.emit(v)

    def _flip_to_manual(self) -> None:
        """Grabbing the handle in auto takes over at tracked position."""
        if not self.is_auto:
            return
        self.mode_sel.set_value("manual")
        self._style_slider()
        self.changed.emit(self._value())

    def _style_slider(self) -> None:
        """Dim the slider while it merely tracks auto value."""
        auto = self.is_auto
        if self.slider.property("auto") != auto:
            self.slider.setProperty("auto", auto)
            self.slider.style().unpolish(self.slider)
            self.slider.style().polish(self.slider)
