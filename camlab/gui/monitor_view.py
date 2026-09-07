# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""MonitorView: display-only twin of the panel UI for the monitor in Both mode.

Same strip, viewfinder, control chips and assists as the panel at REGULAR
density. Consumer driven: its own 10 Hz tick reads the engine and the panel's
monitor sheet state.
"""

from __future__ import annotations

from collections.abc import Callable

from ..qt import Qt, QtCore, QtWidgets
from ..settings import MonitorState
from ..stats import RpiStats
from . import icons
from .chips import CTRL_SPEC, chip_sample, chip_text
from .rpi_stats import field_texts
from .status_strip import StatusStrip
from .style import REGULAR, build_stylesheet
from .viewfinder_area import ViewfinderArea
from .widgets import repolish, vline

# Amber manual accent, same as the panel chips
_ACCENT_ON = "#e5c07b"
_ACCENT_OFF = "#d7dae0"


class MonitorView(QtWidgets.QWidget):
    def __init__(
        self,
        engine,
        focus_sampler,
        monitor_state: Callable[[], MonitorState],
        parent: QtWidgets.QWidget | None = None,
    ):
        super().__init__(parent)
        self._engine = engine
        self._monitor_state = monitor_state
        self._assists: MonitorState | None = None
        self._chip_values: dict[str, float] = {}
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        # Own stylesheet, the panel's compact skin stops at the pane edge
        self.setStyleSheet(build_stylesheet(REGULAR))

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.status = StatusStrip()
        root.addWidget(self.status)

        live = engine.make_mirror() if engine.picam2 is not None else None
        self.viewfinder_area = ViewfinderArea(engine, live=live)
        self.viewfinder_area.apply_profile(REGULAR)
        root.addWidget(self.viewfinder_area, 1)

        root.addWidget(self._build_chips())
        self._reserve_chip_widths()
        focus_sampler.sample.connect(lambda s: self.viewfinder_area.update_focus_map(s.heat))

        # Same cadences as the panel: telemetry at 10 Hz, board loads at 1 Hz
        self._rpi_stats = RpiStats()
        self._tick_timer = self._timer(100, self._tick)
        self._stats_timer = self._timer(1000, self._sample_rpi)
        self._tick()

    def _timer(self, ms: int, slot) -> QtCore.QTimer:
        timer = QtCore.QTimer(self)
        timer.setInterval(ms)
        timer.timeout.connect(slot)
        return timer

    def _build_chips(self) -> QtWidgets.QFrame:
        controls = QtWidgets.QFrame()
        controls.setObjectName("controls")
        row = QtWidgets.QHBoxLayout(controls)
        margin = REGULAR.row_margin
        row.setContentsMargins(margin, 6, margin, 6)
        row.setSpacing(REGULAR.row_spacing)
        ranges = self._engine.control_ranges()
        px = REGULAR.icon_px
        self.mode_btn = QtWidgets.QPushButton(icons.icon("tune", px), "")
        self.mode_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mode_btn.setIconSize(QtCore.QSize(px, px))
        row.addWidget(self.mode_btn)
        pad = REGULAR.divider_pad
        row.addSpacing(pad)
        row.addWidget(vline())
        row.addSpacing(pad)
        self._chips: dict[str, QtWidgets.QPushButton] = {}
        for key in CTRL_SPEC:
            btn = QtWidgets.QPushButton()
            btn.setObjectName("chip")
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.setIconSize(QtCore.QSize(px, px))
            btn.setVisible(key in ranges)
            row.addWidget(btn)
            self._chips[key] = btn
        row.addStretch(1)
        return controls

    def _reserve_chip_widths(self) -> None:
        """Pin each chip to its widest realistic value so live data never moves the row."""
        for key, spec in CTRL_SPEC.items():
            btn = self._chips[key]
            btn.ensurePolished()  # sizeHint must measure with the QSS font
            btn.setText(chip_sample(spec, False))
            btn.setMinimumWidth(btn.sizeHint().width())

    # Ticks run only while on screen
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._tick()
        self._tick_timer.start()
        self._stats_timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._tick_timer.stop()
        self._stats_timer.stop()

    def _tick(self) -> None:
        t = self._engine.telemetry
        md = t.metadata or {}
        self.status.set_telemetry(
            t.frame,
            t.fps if t.fps > 0 else None,
            md.get("ExposureTime"),
            md.get("AnalogueGain"),
            md.get("DigitalGain"),
        )
        self.status.set_temperature(md.get("SensorTemperature"))
        mode = self._engine.current_mode
        text = " Mode: --" if mode is None else f" Mode: {mode.label()}"
        if self.mode_btn.text() != text:
            self.mode_btn.setText(text)
        self._sync_assists()
        if self._engine.latest_histogram is not None:
            self.viewfinder_area.update_histogram(self._engine.latest_histogram)
        self._render_chips()

    def _sample_rpi(self) -> None:
        self.status.set_rpi_stats(field_texts(self._rpi_stats.sample()))

    def _sync_assists(self) -> None:
        """Both heads draw the assists picked on the panel's monitor sheet."""
        state = self._monitor_state()
        if state == self._assists:
            return
        self._assists = state
        self.viewfinder_area.set_assists(state.peaking, state.zebra, state.zebra_threshold)
        self.viewfinder_area.set_histogram_enabled(state.histogram)
        self.viewfinder_area.set_focus_map_enabled(state.focus_map)

    def _render_chips(self) -> None:
        """Same sources as the panel: metadata for value, control state for accent."""
        md = self._engine.telemetry.metadata or {}
        for key, spec in CTRL_SPEC.items():
            value = md.get(spec.md_key)
            if value is None:
                # Metadata drops keys across a pipeline restart, hold the last reading
                value = self._chip_values.get(key)
            else:
                self._chip_values[key] = value
            btn = self._chips[key]
            text = chip_text(spec, value, False)
            if btn.text() != text:
                btn.setText(text)
                btn.setMinimumWidth(max(btn.minimumWidth(), btn.sizeHint().width()))
            manual = getattr(self._engine.control_state, key) is not None
            if btn.property("manual") != manual:
                btn.setProperty("manual", manual)
                color = _ACCENT_ON if manual else _ACCENT_OFF
                btn.setIcon(icons.icon(spec.glyph, REGULAR.icon_px, color))
                repolish(btn)
