# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Sheets docked over viewfinder bottom edge: camera controls and display assists.

Plain Qt widgets over in-scene viewfinder. ControlSheet: auto/manual + slider,
log scale for exposure/gain. MonitorSheet: focus peaking and zebra after cinepi-kurokesu.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from ..qt import Qt, QtCore, QtGui, QtWidgets, Signal
from . import icons
from .style import GLASS_BG, GLASS_BORDER, UiProfile
from .widgets import SegmentedSelector, repolish

# Slider resolution. 1000 steps is finer than any bench display is wide.
_STEPS = 1000


def fmt_exposure(us: float) -> str:
    if us >= 1_000_000:
        return f"{us / 1_000_000:.2f} s"
    if us >= 10000:
        return f"{us / 1000:.1f} ms"
    if us >= 1000:
        return f"{us / 1000:.2f} ms"
    return f"{round(us)} \u00b5s"


def fmt_gain(gain: float) -> str:
    return f"{gain:.2f}x"


def fmt_ct(kelvin: float) -> str:
    return f"{round(kelvin)} K"


class JumpSlider(QtWidgets.QSlider):
    """Absolute pointing: press and drag put handle at cursor.

    Stock QSlider only drags from handle and page-steps elsewhere. Own mouse
    so every press grabs and maps through groove rect.
    """

    def _value_at(self, x: float) -> int:
        opt = QtWidgets.QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(
            QtWidgets.QStyle.ComplexControl.CC_Slider,
            opt,
            QtWidgets.QStyle.SubControl.SC_SliderGroove,
            self,
        )
        handle = self.style().subControlRect(
            QtWidgets.QStyle.ComplexControl.CC_Slider,
            opt,
            QtWidgets.QStyle.SubControl.SC_SliderHandle,
            self,
        )
        pos = round(x) - groove.x() - handle.width() // 2
        span = groove.width() - handle.width()
        return QtWidgets.QStyle.sliderValueFromPosition(
            self.minimum(), self.maximum(), pos, span, opt.upsideDown
        )

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        event.accept()
        self.setSliderDown(True)  # emits sliderPressed
        self.setValue(self._value_at(event.position().x()))

    def mouseMoveEvent(self, event) -> None:
        if not self.isSliderDown():
            super().mouseMoveEvent(event)
            return
        event.accept()
        self.setValue(self._value_at(event.position().x()))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        event.accept()
        self.setSliderDown(False)  # emits sliderReleased


def _jump_slider() -> JumpSlider:
    slider = JumpSlider(Qt.Orientation.Horizontal)
    slider.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    slider.setCursor(Qt.CursorShape.PointingHandCursor)
    return slider


def _sheet_title(text: str) -> QtWidgets.QLabel:
    lbl = QtWidgets.QLabel(text)
    lbl.setObjectName("sheetTitle")
    return lbl


class SheetCard(QtWidgets.QFrame):
    """Translucent bar docked to viewfinder's bottom edge, shared by all sheets.

    Square corners and full width, so it reads as part of the controls bar
    below it. Only the top edge gets a hairline: it is the one border facing
    the live picture."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("controlSheet")

    def paintEvent(self, _event) -> None:
        p = QtGui.QPainter(self)
        p.fillRect(self.rect(), GLASS_BG)
        p.setPen(QtGui.QPen(GLASS_BORDER, 1))
        p.drawLine(0, 0, self.width(), 0)
        p.end()


class ControlSheet(SheetCard):
    """[Auto|Manual] selector + slider + value label for one control."""

    changed = Signal(object)  # None = auto, else the manual value

    def __init__(
        self,
        title: str,
        fmt: Callable[[float], str],
        log_scale: bool = False,
        integer: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self._fmt = fmt
        self._log = log_scale
        self._int = integer
        self._lo = 1.0
        self._hi = 2.0
        self._tracking = False  # programmatic slider move, not a user action

        self.title_lbl = _sheet_title(title)

        self.mode_sel = SegmentedSelector()
        self.mode_sel.set_options(
            [("Auto", "auto"), ("Manual", "manual")], current="auto", stretch=False
        )
        self.mode_sel.changed.connect(self._on_mode)

        self.slider = _jump_slider()
        self.slider.setRange(0, _STEPS)
        self.slider.valueChanged.connect(self._on_slider)
        self.slider.sliderPressed.connect(self._flip_to_manual)

        self.value_lbl = QtWidgets.QLabel("--")
        self.value_lbl.setObjectName("sheetValue")
        self.value_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(14)
        row.addWidget(self.title_lbl)
        row.addWidget(self.mode_sel)
        row.addWidget(self.slider, 1)
        row.addWidget(self.value_lbl)

    def apply_profile(self, profile: UiProfile) -> None:
        self.title_lbl.setMinimumWidth(profile.sheet_title_w)
        self.value_lbl.setMinimumWidth(profile.sheet_value_w)

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
        return round(v) if self._int else v

    def _to_pos(self, value) -> int:
        v = min(max(float(value), self._lo), self._hi)
        if self._log:
            t = math.log(v / self._lo) / math.log(self._hi / self._lo)
        else:
            t = (v - self._lo) / (self._hi - self._lo)
        return round(t * _STEPS)

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
            repolish(self.slider)


# Zebra clip-threshold span (percent of full scale), matching cinepi's
# slider (0.7..1.0, default 0.95).
_ZEBRA_LO, _ZEBRA_HI, _ZEBRA_DEFAULT = 70, 100, 95


class MonitorSheet(SheetCard):
    """Focus peaking and zebra toggles + zebra threshold slider."""

    changed = Signal(bool, bool, float)  # (peaking, zebra, zebra_threshold 0..1)

    def __init__(self, parent=None):
        super().__init__(parent)

        self.title_lbl = _sheet_title("Monitor")

        # Icons come from apply_profile, rasterised at profile size.
        self.peak_btn = QtWidgets.QPushButton(" Focus Peaking")
        self.zebra_btn = QtWidgets.QPushButton(" Zebra")
        for btn in (self.peak_btn, self.zebra_btn):
            # Segment look (square corners) to match the Auto/Manual rows.
            # No pos property, so these stay visually separate toggles.
            btn.setObjectName("segment")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            btn.toggled.connect(self._emit)

        self.thr_lbl = QtWidgets.QLabel("Threshold")
        self.thr_lbl.setObjectName("sheetCaption")

        self.slider = _jump_slider()
        self.slider.setRange(_ZEBRA_LO, _ZEBRA_HI)
        self.slider.setValue(_ZEBRA_DEFAULT)
        self.slider.valueChanged.connect(self._on_slider)

        self.value_lbl = QtWidgets.QLabel(f"{_ZEBRA_DEFAULT}%")
        self.value_lbl.setObjectName("sheetValue")
        # Fixed width so the cluster holds still as digits change.
        self.value_lbl.setMinimumWidth(48)
        self.value_lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(14)
        row.addWidget(self.title_lbl)
        row.addWidget(self.peak_btn)
        # Threshold cluster hugs the Zebra button (tighter spacing than the
        # row) and dims with it, so it reads as that button's parameter.
        cluster = QtWidgets.QHBoxLayout()
        cluster.setSpacing(8)
        cluster.addWidget(self.zebra_btn)
        cluster.addSpacing(4)
        cluster.addWidget(self.thr_lbl)
        cluster.addWidget(self.slider)
        cluster.addWidget(self.value_lbl)
        row.addLayout(cluster)
        row.addStretch(1)
        self._style_slider()

    def apply_profile(self, profile: UiProfile) -> None:
        self.title_lbl.setMinimumWidth(profile.sheet_title_w)
        self.slider.setFixedWidth(profile.zebra_slider_w)
        icon_px = profile.icon_px - 2
        for btn, name in ((self.peak_btn, "center_focus_weak"), (self.zebra_btn, "texture")):
            btn.setIcon(icons.icon(name, icon_px))
            btn.setIconSize(QtCore.QSize(icon_px, icon_px))

    @property
    def peaking(self) -> bool:
        return self.peak_btn.isChecked()

    @property
    def zebra(self) -> bool:
        return self.zebra_btn.isChecked()

    @property
    def zebra_threshold(self) -> float:
        return self.slider.value() / 100.0

    def _on_slider(self, _pos: int) -> None:
        self.value_lbl.setText(f"{self.slider.value()}%")
        # Touching the threshold implies wanting zebra on (mirrors control
        # sheets, where a slider touch flips auto to manual).
        if not self.zebra_btn.isChecked():
            self.zebra_btn.setChecked(True)  # toggled re-emits
        else:
            self._emit()

    def _emit(self) -> None:
        self._style_slider()
        self.changed.emit(self.peaking, self.zebra, self.zebra_threshold)

    def _style_slider(self) -> None:
        """Dim the threshold cluster while zebra is off (slider reuses the
        auto styling, labels a dim property)."""
        dim = not self.zebra_btn.isChecked()
        if self.slider.property("auto") == dim:
            return
        self.slider.setProperty("auto", dim)
        self.thr_lbl.setProperty("dim", dim)
        self.value_lbl.setProperty("dim", dim)
        repolish(self.slider, self.thr_lbl, self.value_lbl)
