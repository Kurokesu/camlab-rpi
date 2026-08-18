# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""MainWindow - fullscreen bench UI: viewfinder + status strip + controls + log."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import ClassVar, NamedTuple

from .. import network, updater
from ..camera import CameraEngine
from ..config_manager import ConfigManager, poweroff
from ..display import Backlight, DisplayManager
from ..drm import dsi_blocked_ports
from ..dsi_panels import PanelRegistry
from ..integrity import IntegrityMonitor, LogClassifier, StderrCapture
from ..modes import mode_for
from ..qt import Qt, QtCore, QtGui, QtWidgets, Signal, Slot
from ..sensors import SensorRegistry
from ..settings import SettingsStore
from ..stats import RpiStats
from . import icons
from .about_dialog import AboutCard
from .control_sheet import ControlSheet, MonitorSheet, fmt_ct, fmt_exposure, fmt_gain
from .covers import BootCover, SwitchCover
from .log_panel import LogPanel
from .mode_dialog import ModeCard
from .overlay import ModalOverlay, message_card
from .rpi_stats import field_texts
from .sensor_dialog import SensorCard
from .settings_dialog import SettingsCard
from .status_strip import StatusStrip
from .style import SEV_COLOR, UiProfile, build_stylesheet, forced_screen, profile_for_screen
from .viewfinder_area import ViewfinderArea
from .widgets import repolish, vline

log = logging.getLogger(__name__)

# Amber = "not showing the plain picture" (manual control, assist overlay).
_ACCENT_ON = "#e5c07b"
_ACCENT_OFF = "#d7dae0"

# Long enough for the card to reach the panel before a blocking call starts.
_PAINT_MS = 80


class _ChipSpec(NamedTuple):
    """One camera-control chip: label, icon, metadata source, formatting."""

    label: str
    glyph: str
    md_key: str
    fmt: Callable[[float], str]
    sample: str  # widest realistic value, pins chip width


class MainWindow(QtWidgets.QMainWindow):
    first_frame = Signal(float)

    _CTRL_SPEC: ClassVar[dict[str, _ChipSpec]] = {
        "exposure_us": _ChipSpec("Exp", "shutter_speed", "ExposureTime", fmt_exposure, "888.8 ms"),
        "gain": _ChipSpec("Gain", "iso", "AnalogueGain", fmt_gain, "88.88x"),
        "colour_temp": _ChipSpec("WB", "wb_sunny", "ColourTemperature", fmt_ct, "8888 K"),
    }

    def __init__(
        self,
        engine: CameraEngine,
        registry: SensorRegistry,
        panels: PanelRegistry,
        config: ConfigManager,
        capture: StderrCapture,
        classifier: LogClassifier,
        settings: SettingsStore,
        display_manager: DisplayManager | None = None,
        backlight: Backlight | None = None,
    ):
        super().__init__()
        self.engine = engine
        self.registry = registry
        self.panels = panels
        self.config = config
        self.capture = capture
        self.settings = settings
        self.monitor = IntegrityMonitor(classifier)
        self._overlay: ModalOverlay | None = None
        self._mode_avail = (0, 0)  # viewfinder size captured when mode card opens
        self._engine_started = False
        self._backlight = backlight
        # Output policy settles before Qt starts, boot-time primary screen is right one.
        self._profile: UiProfile = profile_for_screen(QtWidgets.QApplication.primaryScreen())
        self._display_key: tuple | None = None
        self._sev = ""  # worst severity seen, tints log button
        self._log_btn_state: tuple | None = None  # last synced look, skips no-op restyles
        self._chip_values: dict[str, float] = {}  # last metadata reading per chip

        self.setWindowTitle("camlab")
        self.setStyleSheet(build_stylesheet(self._profile))

        central = QtWidgets.QWidget()
        forced = forced_screen()
        if forced is None:
            self.setCentralWidget(central)
        else:
            # Panel preview. Cage forces fullscreen, so the UI shrinks, not the window.
            central.setFixedSize(*forced)
            wrapper = QtWidgets.QWidget()
            wrapper.setObjectName("previewBackdrop")
            wrapper.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            # Selector scopes the black to the backdrop, a bare rule cascades.
            wrapper.setStyleSheet("QWidget#previewBackdrop { background: #000; }")
            grid = QtWidgets.QGridLayout(wrapper)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.addWidget(central, 0, 0, Qt.AlignmentFlag.AlignCenter)
            self.setCentralWidget(wrapper)
        # Focus sink: empty chrome click parks focus here, not on button.
        central.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.status = StatusStrip()
        self.status.set_compact(self._profile.compact)
        root.addWidget(self.status)

        self.viewfinder_area = ViewfinderArea(engine)
        self.viewfinder_area.apply_profile(self._profile)
        self.viewfinder_area.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding
        )
        root.addWidget(self.viewfinder_area, 1)

        self._build_sheets()
        root.addWidget(self._build_controls_row())

        # Log panel starts collapsed. Equal stretch shrinks viewfinder when open.
        self.log_panel = LogPanel(classifier)
        self.log_panel.setVisible(False)
        root.addWidget(self.log_panel, 1)

        self._wire()
        self._populate_static()
        # Histogram overlay, persisted app-wide, default off.
        self._histogram_on = settings.get_histogram()
        self._sheets["monitor"].set_histogram(self._histogram_on)
        if self._histogram_on:
            self.engine.set_stats_output(True)
            self.viewfinder_area.set_histogram_enabled(True)
        # Start on inert sink so nothing highlighted until Tab.
        central.setFocus(Qt.FocusReason.OtherFocusReason)

        self._build_shortcuts()
        self._build_timers()

        # Black covers over chrome: boot until first fullscreen, switch across a hotplug.
        self._boot_cover: BootCover | None = BootCover(central, self)
        self._boot_cover.revealed.connect(self._on_boot_revealed)
        self._switch_cover = SwitchCover(central, self)

        self._watch_screens()
        if display_manager is not None:
            display_manager.display_changed.connect(self._on_display_changed)

    # construction
    def _build_sheets(self) -> None:
        # Sheets dock over viewfinder bottom edge. Exposure and gain span decades, log sliders.
        self._sheets: dict[str, QtWidgets.QWidget] = {
            "exposure_us": ControlSheet("Exposure", fmt_exposure, log_scale=True, parent=self),
            "gain": ControlSheet("Gain", fmt_gain, log_scale=True, integer=False, parent=self),
            "colour_temp": ControlSheet("White balance", fmt_ct, parent=self),
            "monitor": MonitorSheet(parent=self),
        }
        self._open_sheet: str | None = None
        for sheet in self._sheets.values():
            sheet.setVisible(False)
            sheet.apply_profile(self._profile)
        for key in self._CTRL_SPEC:
            self._sheets[key].changed.connect(lambda v, k=key: self._on_control_changed(k, v))
        monitor = self._sheets["monitor"]
        monitor.changed.connect(self._on_monitor_changed)
        monitor.histogram_changed.connect(self._apply_histogram)
        # Keep open sheet glued to viewfinder bottom edge on resize.
        self.viewfinder_area.installEventFilter(self)

    def _build_controls_row(self) -> QtWidgets.QFrame:
        # Sensor/Mode merge status and chooser. Divider fences Shutdown against mis-clicks.
        controls = QtWidgets.QFrame()
        controls.setObjectName("controls")
        crow = QtWidgets.QHBoxLayout(controls)
        self._crow = crow
        self._divider_pads: list[QtWidgets.QSpacerItem] = []
        px = self._profile.icon_px
        self.sensor_btn = QtWidgets.QPushButton()
        self.sensor_btn.clicked.connect(self._choose_sensor)
        self.mode_btn = QtWidgets.QPushButton()
        self.mode_btn.clicked.connect(self._choose_mode)
        self.mode_btn.setEnabled(bool(self.engine.modes))
        # Control chips: live value on button, amber when manual, click opens sheet.
        # Born bare, _populate_static renders icon and placeholder before first paint.
        self._ctrl_buttons: dict[str, QtWidgets.QPushButton] = {
            key: QtWidgets.QPushButton() for key in self._CTRL_SPEC
        }
        self.monitor_btn = QtWidgets.QPushButton(icons.icon("stroke_partial", px), " Monitor")
        self._sheet_buttons = dict(self._ctrl_buttons, monitor=self.monitor_btn)
        for key, btn in self._sheet_buttons.items():
            btn.setCheckable(True)
            # Chip styling anchors left so icon and label hold still as value grows.
            btn.setObjectName("chip")
            btn.clicked.connect(lambda _=False, k=key: self._toggle_sheet(k))
        self.settings_btn = QtWidgets.QPushButton(icons.icon("settings", px), " Settings")
        self.settings_btn.clicked.connect(self._open_settings)
        self.log_btn = QtWidgets.QPushButton(icons.icon("terminal", px), " Log")
        self.log_btn.setCheckable(True)
        self.log_btn.toggled.connect(self._toggle_log)
        self.shutdown_btn = QtWidgets.QPushButton(
            icons.icon("power_settings_new", px, "#d98b80"), " Shutdown"
        )
        self.shutdown_btn.setObjectName("danger")
        self.shutdown_btn.clicked.connect(self._shutdown)

        self._chrome_btns = (
            self.sensor_btn,
            self.mode_btn,
            *self._ctrl_buttons.values(),
            self.monitor_btn,
            self.settings_btn,
            self.log_btn,
            self.shutdown_btn,
        )
        # QPushButton clamps icon to small default, set size explicitly.
        # TabFocus keeps mouse click from leaving focus ring.
        for btn in self._chrome_btns:
            btn.setIconSize(QtCore.QSize(px, px))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)

        # Sensor and Mode read as one group, so no divider between.
        crow.addWidget(self.sensor_btn)
        crow.addWidget(self.mode_btn)
        self._add_divider(vline())
        for btn in self._ctrl_buttons.values():
            crow.addWidget(btn)
        crow.addWidget(self.monitor_btn)
        # Stretch splits evenly around divider, keeping it centered in gap.
        self._mid_divider = vline()
        crow.addStretch(1)
        self._add_divider(self._mid_divider)
        crow.addStretch(1)
        crow.addWidget(self.settings_btn)
        crow.addWidget(self.log_btn)
        self._add_divider(vline())
        crow.addWidget(self.shutdown_btn)
        self._apply_row_metrics()
        return controls

    def _add_divider(self, divider: QtWidgets.QFrame) -> None:
        """Hairline plus its padding, tracked so a profile flip can retune the gap."""
        pads = (self._pad_item(), self._pad_item())
        self._crow.addItem(pads[0])
        self._crow.addWidget(divider)
        self._crow.addItem(pads[1])
        self._divider_pads.extend(pads)

    @staticmethod
    def _pad_item() -> QtWidgets.QSpacerItem:
        return QtWidgets.QSpacerItem(
            0,
            0,
            QtWidgets.QSizePolicy.Policy.Fixed,
            QtWidgets.QSizePolicy.Policy.Minimum,
        )

    def _build_shortcuts(self) -> None:
        # Window shortcuts fire regardless of child focus, cover main screen and modal overlay.
        esc = QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_Escape), self)
        esc.setContext(Qt.ShortcutContext.WindowShortcut)
        esc.activated.connect(self._on_escape)
        for seq in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            sc = QtGui.QShortcut(QtGui.QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(self._on_return)

    def _build_timers(self) -> None:
        # Telemetry at 10 Hz: about the fastest a changing number stays readable.
        self._status_timer = QtCore.QTimer(self)
        self._status_timer.setInterval(100)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start()

        # Board stats at 1 Hz. Load percentages are deltas, 10 Hz reads as noise.
        self._rpi_stats = RpiStats()
        self._rpi_timer = QtCore.QTimer(self)
        self._rpi_timer.setInterval(1000)
        self._rpi_timer.timeout.connect(self._sample_rpi)
        self._rpi_timer.start()

        # Debounce persistence so slider drag is one write, not one per tick.
        self._persist_timer = QtCore.QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.setInterval(500)
        self._persist_timer.timeout.connect(self._persist_controls)

        # Backlight writes are live during drag, persistence is debounced.
        self._backlight_pct: int | None = None
        self._backlight_persist = QtCore.QTimer(self)
        self._backlight_persist.setSingleShot(True)
        self._backlight_persist.setInterval(500)
        self._backlight_persist.timeout.connect(self._persist_backlight)

        # Refit lores once geometry stops moving. Display switches arrive as a resize burst.
        self._refit_timer = QtCore.QTimer(self)
        self._refit_timer.setSingleShot(True)
        self._refit_timer.setInterval(500)
        self._refit_timer.timeout.connect(self._refit_lores)

    def _watch_screens(self) -> None:
        # Re-assert fullscreen whenever screen topology changes.
        app = QtWidgets.QApplication.instance()
        app.screenAdded.connect(self._on_screen_added)
        app.screenRemoved.connect(self._on_screen_removed)
        app.primaryScreenChanged.connect(lambda _s: self._resync_fullscreen())
        for scr in app.screens():
            scr.geometryChanged.connect(lambda _g: self._resync_fullscreen())

    # wiring
    def _wire(self) -> None:
        self.capture.line_received.connect(self.log_panel.append_line)
        self.capture.line_received.connect(self.monitor.feed)
        self.monitor.stats_changed.connect(self.log_panel.update_integrity)
        self.monitor.stats_changed.connect(self._on_integrity)
        self.status.stats_tapped.connect(self.viewfinder_area.toggle_stats_overlay)
        self.viewfinder_area.tapped.connect(self._on_viewfinder_tapped)
        # Clearing the view resets counts, so the two never disagree.
        self.log_panel.cleared.connect(self.monitor.reset)
        self.first_frame.connect(self._on_first_frame)
        self.engine.on_first_frame(lambda boot_time: self.first_frame.emit(boot_time))

    @staticmethod
    def _is_mono(sensor, options: list[str]) -> bool:
        """True if sensor's mono overlay param is active in config.txt."""
        return bool(sensor and sensor.mono_option and sensor.mono_option in options)

    def _populate_static(self) -> None:
        self._apply_chrome_texts()
        self._refresh_sensor_status()
        self._refresh_mode_status()
        self._refresh_control_buttons()
        self._render_chips()
        self._reserve_chip_widths()

    def _apply_chrome_texts(self) -> None:
        """Static button labels: full words on a monitor, icon-only compact."""
        compact = self._profile.compact
        self.monitor_btn.setText("" if compact else " Monitor")
        if self.monitor_btn.property("iconOnly") != compact:
            self.monitor_btn.setProperty("iconOnly", compact)
            repolish(self.monitor_btn)
        self.settings_btn.setText("" if compact else " Settings")
        self.shutdown_btn.setText("" if compact else " Shutdown")
        self._sync_log_button(self.log_btn.isChecked())

    def _apply_row_metrics(self) -> None:
        """Controls-row density: compact packs tighter so 800 px fits."""
        m = self._profile.row_margin
        self._crow.setContentsMargins(m, 6, m, 6)
        self._crow.setSpacing(self._profile.row_spacing)
        pad = self._profile.divider_pad
        for item in self._divider_pads:
            item.changeSize(
                pad, 0, QtWidgets.QSizePolicy.Policy.Fixed, QtWidgets.QSizePolicy.Policy.Minimum
            )
        self._crow.invalidate()
        self._mid_divider.setVisible(self._profile.compact)

    def _refresh_sensor_status(self) -> None:
        """Update the merged Sensor chip: selection text plus a detection glyph."""
        cur = self.config.get_current()
        sensor = self.registry.by_overlay(cur["overlay"]) if cur["overlay"] else None
        name = sensor.name if sensor else (cur["overlay"] or "unknown")
        variant = ", mono" if self._is_mono(sensor, cur["options"]) else ""
        if self._profile.compact:
            # Compact keeps just name. Port and variant live in dialog.
            self.sensor_btn.setText(f" {name}")
        else:
            self.sensor_btn.setText(f" Sensor: {name} ({cur['port']}{variant})")

        detected = self.engine.info.model if self.engine.info is not None else None
        overlay = cur["overlay"]
        if not detected:
            glyph, color, tip = "error", "#e06c75", "No camera detected by libcamera"
            # On panel rigs the usual cause is the display taking the configured port.
            blocked = dsi_blocked_ports()
            if cur["port"] in blocked:
                tip += f". {cur['port']} is claimed by the display overlay, move the camera"
        elif overlay and detected.lower() == overlay.lower():
            glyph, color, tip = (
                "check_circle",
                "#98c379",
                f"Detected {detected} (matches selection)",
            )
        elif overlay:
            glyph, color, tip = (
                "warning",
                "#e5c07b",
                f"Detected {detected}, selection is {overlay}",
            )
        else:
            glyph, color, tip = "photo_camera", "#aeb4bf", f"Detected {detected}"
        self.sensor_btn.setIcon(icons.icon(glyph, self._profile.icon_px, color))
        self.sensor_btn.setToolTip(tip)

    def _refresh_mode_status(self) -> None:
        """Update the merged Mode chip. Compact drops the format token."""
        m = self.engine.sensor_mode
        if m and m.get("format") and m.get("size"):
            w, h = m["size"]
            if self._profile.compact:
                self.mode_btn.setText(f" {w}x{h}")
            else:
                self.mode_btn.setText(f" Mode: {m['format']} {w}x{h}")
        else:
            self.mode_btn.setText(" --" if self._profile.compact else " Mode: --")
        self.mode_btn.setIcon(icons.icon("tune", self._profile.icon_px))

    def _refresh_control_buttons(self) -> None:
        """Show a control chip only when the camera offers that control."""
        ranges = self.engine.control_ranges()
        for key, btn in self._ctrl_buttons.items():
            btn.setVisible(key in ranges)
        # Monitor shaders draw on live stream, any camera qualifies.
        self.monitor_btn.setVisible(self.viewfinder_area.has_camera)

    # slots
    def _sample_rpi(self) -> None:
        """One sample rendered once, feeds the strip cluster and the overlay card."""
        texts = field_texts(self._rpi_stats.sample())
        self.status.set_rpi_stats(texts)
        self.viewfinder_area.update_stats(texts)

    @Slot(float)
    def _on_first_frame(self, boot_time: float) -> None:
        self.log_panel.set_boot_time(boot_time)
        log.info("first frame at boot time=%.1fs", boot_time)

    @Slot(object)
    def _on_integrity(self, stats) -> None:
        self._sev = "error" if stats.errors else ("warning" if stats.warnings else "")
        self._sync_log_button(self.log_btn.isChecked())

    def _update_status(self) -> None:
        # One snapshot read: frame, fps and metadata from same published frame.
        t = self.engine.telemetry
        md = t.metadata or {}
        self.status.set_telemetry(
            t.frame,
            t.fps if t.fps > 0 else None,
            md.get("ExposureTime"),
            md.get("AnalogueGain"),
            md.get("DigitalGain"),
        )
        # Not every sensor offers SensorTemperature. None keeps the last reading.
        self.status.set_temperature(md.get("SensorTemperature"))
        # Engine latches ISP histogram off any frame carrying stats, survives frames
        # without blob (libcamera skips some above 30 fps).
        if self._histogram_on and self.engine.latest_histogram is not None:
            self.viewfinder_area.update_histogram(self.engine.latest_histogram)
        self._render_chips()

    def _render_chips(self) -> None:
        """Chips carry live values, open sheet tracks value in auto.

        Metadata drops keys across a pipeline restart, so a gap keeps the last
        reading rather than flashing the placeholder.
        """
        md = self.engine.telemetry.metadata or {}
        for key, spec in self._CTRL_SPEC.items():
            value = md.get(spec.md_key)
            if value is None:
                value = self._chip_values.get(key)
            else:
                self._chip_values[key] = value
            self._render_chip(key, value)
            if key == self._open_sheet:
                self._sheets[key].set_live(value)

    def _render_chip(self, key: str, value: float | None) -> None:
        """Value text ("--" until metadata) and manual accent. Compact drops label."""
        spec = self._CTRL_SPEC[key]
        body = spec.fmt(value) if value is not None else "--"
        text = f" {body}" if self._profile.compact else f" {spec.label} {body}"
        btn = self._ctrl_buttons[key]
        if btn.text() != text:
            btn.setText(text)
            # Ratchet width so a metadata gap never shrinks a chip and shifts neighbours.
            btn.setMinimumWidth(max(btn.minimumWidth(), btn.sizeHint().width()))
        self._set_chip_accent(btn, spec.glyph, getattr(self.engine.control_state, key) is not None)

    def _reserve_chip_widths(self) -> None:
        """Pin each chip to its widest realistic value so live data never moves the row."""
        for key, spec in self._CTRL_SPEC.items():
            btn = self._ctrl_buttons[key]
            btn.ensurePolished()  # sizeHint must measure with the QSS font
            sample = f" {spec.sample}" if self._profile.compact else f" {spec.label} {spec.sample}"
            current = btn.text()
            btn.setText(sample)
            btn.setMinimumWidth(btn.sizeHint().width())
            btn.setText(current)

    def _set_chip_accent(self, btn: QtWidgets.QPushButton, glyph: str, active: bool) -> None:
        """Amber accent on/off. Re-polish and re-tint only on a flip."""
        if btn.property("manual") == active:
            return
        btn.setProperty("manual", active)
        btn.setIcon(icons.icon(glyph, self._profile.icon_px, _ACCENT_ON if active else _ACCENT_OFF))
        repolish(btn)

    def _toggle_log(self, checked: bool) -> None:
        self.log_panel.setVisible(checked)
        self._sync_log_button(checked)

    def _sync_log_button(self, checked: bool) -> None:
        # Same button closes panel: open state reads as pressed toggle and relabels.
        # Closed, carries severity tint so trouble shows unopened.
        # Integrity ticks mostly re-report the same severity, skip those.
        compact = self._profile.compact
        state = (checked, self._sev, compact, self._profile.icon_px)
        if state == self._log_btn_state:
            return
        self._log_btn_state = state
        color = SEV_COLOR.get(self._sev, _ACCENT_OFF)
        if checked:
            self.log_btn.setIcon(icons.icon("close", self._profile.icon_px))
            self.log_btn.setText("" if compact else " Close log")
        else:
            self.log_btn.setIcon(icons.icon("terminal", self._profile.icon_px, color))
            self.log_btn.setText("" if compact else " Log")
        self.log_btn.setProperty("sev", self._sev or None)
        repolish(self.log_btn)

    # control sheets (floating, viewfinder stays live)
    def _toggle_sheet(self, key: str) -> None:
        if self._open_sheet == key:
            self._close_sheet()
        else:
            self._show_sheet(key)

    def _show_sheet(self, key: str) -> None:
        self._open_sheet = key
        if key in self._ctrl_buttons:
            self._seed_sheet(key)  # monitor sheet holds its own state
        self._position_sheet(key)
        for k, sheet in self._sheets.items():
            sheet.setVisible(k == key)
        self._sheets[key].raise_()
        for k, btn in self._sheet_buttons.items():
            btn.setChecked(k == key)

    def _position_sheet(self, key: str) -> None:
        """Dock the sheet to viewfinder's bottom edge, flush with the controls bar."""
        sheet = self._sheets[key]
        h = sheet.sizeHint().height()
        pa = self.viewfinder_area
        origin = pa.mapTo(self, QtCore.QPoint(0, 0))
        sheet.setGeometry(origin.x(), origin.y() + pa.height() - h, pa.width(), h)

    def eventFilter(self, obj, event) -> bool:
        if (
            obj is self.viewfinder_area
            and event.type() == QtCore.QEvent.Type.Resize
            and self._open_sheet is not None
        ):
            self._position_sheet(self._open_sheet)
        return super().eventFilter(obj, event)

    def _on_viewfinder_tapped(self) -> None:
        if self._open_sheet is not None:
            self._close_sheet()

    def _close_sheet(self) -> None:
        self._open_sheet = None
        for sheet in self._sheets.values():
            sheet.setVisible(False)
        for btn in self._sheet_buttons.values():
            btn.setChecked(False)
        self.centralWidget().setFocus(Qt.FocusReason.OtherFocusReason)

    def _seed_sheet(self, key: str) -> None:
        """Range and state from the engine, silent (no changed emission)."""
        sheet = self._sheets[key]
        rng = self.engine.control_ranges().get(key)
        if rng:
            sheet.set_range(*rng)
        sheet.set_state(getattr(self.engine.control_state, key))

    def _on_monitor_changed(self, peaking: bool, zebra: bool, threshold: float) -> None:
        self.viewfinder_area.set_assists(peaking, zebra, threshold)
        self._refresh_monitor_chip()

    def _refresh_monitor_chip(self) -> None:
        """Amber chip while anything the sheet owns draws, same accent as manual control."""
        monitor = self._sheets["monitor"]
        drawing = monitor.peaking or monitor.zebra or monitor.histogram
        self._set_chip_accent(self.monitor_btn, "stroke_partial", drawing)

    def _on_control_changed(self, key: str, value) -> None:
        # Engine clamps, so reflect what was actually set.
        st = self.engine.set_control_state(**{key: value})
        actual = getattr(st, key)
        if value is not None and actual is not None and actual != value:
            self._sheets[key].set_state(actual)
        self._persist_timer.start()

    def _persist_controls(self) -> None:
        overlay = self.config.get_current().get("overlay") or ""
        st = self.engine.control_state
        self.settings.set_controls(overlay, st.exposure_us, st.gain, st.colour_temp)

    def _flush_pending_persist(self) -> None:
        """Persist a control change still sitting in the debounce window."""
        if self._persist_timer.isActive():
            self._persist_timer.stop()
            self._persist_controls()

    @property
    def _modal_active(self) -> bool:
        return self._overlay is not None

    def _on_return(self) -> None:
        # Activate focused button. In modal, fall back to card primary so Enter works before tabbing.
        # On inert sink, do nothing.
        focused = QtWidgets.QApplication.focusWidget()
        if isinstance(focused, QtWidgets.QPushButton) and focused.isEnabled():
            focused.click()
            return
        if self._overlay is not None:
            primary = getattr(self._overlay.card, "primary_button", None)
            if primary is not None and primary.isEnabled():
                primary.click()

    def _on_escape(self) -> None:
        # Close frontmost open layer, otherwise Escape is kill switch.
        # Immediate poweroff, no confirm by design on power-cycle tool.
        if self._modal_active:
            self._close_modal()
        elif self._open_sheet is not None:
            self._close_sheet()
        elif self.log_btn.isChecked():
            self.log_btn.setChecked(False)
        else:
            self._shutdown()

    # in-window modals: Cage renders separate top-level dialogs as a tiny unusable artifact
    def _open_modal(self, card) -> None:
        if self._modal_active:
            return  # one modal at a time
        # Sheet under backdrop would look interactive, close it. State lives in engine.
        self._close_sheet()
        # Frost viewfinder, leave its area undimmed. Without camera hides placeholder text.
        self.viewfinder_area.set_frost(True)
        clear = None
        if self.viewfinder_area.has_camera:
            clear = self.viewfinder_area.geometry()
        # Overlay traps Tab. Backdrop press cancels, same as Escape. Enter/Escape are shortcuts.
        margin = 16 if self._profile.compact else 40
        self._overlay = ModalOverlay(
            self.centralWidget(),
            card,
            clear_rect=clear,
            margin=margin,
            on_backdrop=self._close_modal,
        )

    def _close_modal(self) -> None:
        if self._overlay is not None:
            self._overlay.dismiss()
            self._overlay = None
        self.viewfinder_area.set_frost(False)
        # Park focus on inert sink, Qt would otherwise restore pre-modal widget.
        self.centralWidget().setFocus(Qt.FocusReason.OtherFocusReason)

    def _show_message(self, title: str, message: str) -> None:
        self._close_modal()
        self._open_modal(message_card(title, message, [("OK", "", self._close_modal)]))

    def _choose_mode(self) -> None:
        if not self.engine.modes:
            self._show_message("No modes", "No selectable sensor modes were enumerated")
            return
        # Viewfinder area at open time sizes the new mode's lores stream.
        self._mode_avail = self.viewfinder_area.lores_size()
        card = ModeCard(
            self.engine.modes,
            self.engine.current_mode,
            self.engine.fps_current,
            self.engine.fps_fixed,
            on_apply=self._apply_mode,
            on_cancel=self._close_modal,
            compact=self._profile.compact,
        )
        self._open_modal(card)

    def _apply_mode(
        self, size: tuple[int, int], bit_depth: int, fps: float, fps_fixed: bool
    ) -> None:
        self._close_modal()
        mode = mode_for(self.engine.modes, tuple(size), int(bit_depth))
        if mode is None:  # re-validate at apply time
            self._show_message("Mode unavailable", "That mode is no longer available")
            return
        try:
            self.engine.apply_mode(mode, float(fps), self._mode_avail, fps_fixed)
        except Exception as exc:
            log.exception("apply mode failed")
            self._show_message("Mode change failed", str(exc))
            return
        # Persist only after a successful reconfigure, never store an unrunnable config.
        overlay = self.config.get_current().get("overlay") or ""
        self.settings.set_mode(overlay, tuple(size), int(bit_depth), float(fps), fps_fixed)
        self.monitor.reset()
        self._refresh_mode_status()
        # A new mode may re-clamp manual values against frame duration, so persist again.
        self._persist_timer.start()

    def _display_name_current(self, disp: dict) -> str | None:
        """Catalog name for the current display block, raw overlay when off-catalog."""
        if not disp["present"] or not disp["overlay"]:
            return None
        panel = self.panels.by_overlay(disp["overlay"].split(",")[0])
        return panel.name if panel else disp["overlay"]

    def _choose_sensor(self) -> None:
        cur = self.config.get_current()
        sensor = self.registry.by_overlay(cur["overlay"]) if cur["overlay"] else None
        mono = self._is_mono(sensor, cur["options"])
        disp = self.config.get_current_display()
        # No block but a live DSI connector: firmware-detected panel, not ours to manage.
        locked_ports = dsi_blocked_ports() if not disp["present"] else set()
        current_display = self._display_name_current(disp)
        # Off-catalog block: its claimed port is fixed, the card locks it out.
        offcat_port = None
        if current_display is not None and self.panels.by_name(current_display) is None:
            offcat_port = disp["port_blocked"]
        card = SensorCard(
            self.registry,
            self.panels,
            sensor.name if sensor else None,
            cur["port"],
            mono,
            current_display,
            display_locked=bool(locked_ports),
            locked_ports=locked_ports,
            offcat_port=offcat_port,
            on_apply=self._apply_sensor,
            on_cancel=self._close_modal,
        )
        self._open_modal(card)

    def _apply_sensor(
        self, sensor_name: str, port: str, mono: bool, display_name: str | None
    ) -> None:
        self._close_modal()
        chosen = self.registry.by_name(sensor_name)
        if chosen is None:
            return
        options = list(chosen.options)
        if mono and chosen.mono_option and chosen.mono_option not in options:
            options.append(chosen.mono_option)
        # Flush before the rewrite: persisted controls key by overlay, about to change.
        self._flush_pending_persist()
        disp = self.config.get_current_display()
        panel = self.panels.by_name(display_name)
        if panel is not None:
            target_raw = ConfigManager.compose_display_overlay(panel.overlay, port)
        elif display_name is not None:  # off-catalog block kept as-is
            target_raw = disp["overlay"]
        else:
            target_raw = None
        display_written = False
        try:
            # Display first, the camera write validates its port against that block.
            if target_raw != disp["overlay"]:
                self.config.apply_display(target_raw)
                display_written = True
            self.config.apply(chosen.overlay, port, options)
        except Exception as exc:  # noqa: BLE001 surface the failure, do not power off
            detail = str(exc)
            log.error("apply failed: %s", detail)
            if display_written:
                # Camera write failed after the display one, undo to avoid a half-apply.
                try:
                    self.config.apply_display(disp["overlay"])
                except Exception:
                    log.exception("display rollback failed")
                    detail += " The display change stuck, re-apply to undo it."
            self._show_message("Apply failed", detail)
            return
        # Power down rather than reboot, rewiring needs the box off.
        poweroff()

    def _open_settings(self) -> None:
        # Also the way back from About, so drop that card first. No-op from chrome.
        self._close_modal()
        # Brightness only while touch panel is active display, HDMI would dim dark one.
        backlight_pct = None
        if self._profile.compact and self._backlight is not None and self._backlight.available:
            backlight_pct = self._backlight.get_percent()
        card = SettingsCard(
            backlight_pct=backlight_pct,
            on_apply_network=self._apply_network,
            on_backlight=self._on_backlight,
            on_cancel=self._close_modal,
            on_about=self._open_about,
        )
        self._open_modal(card)

    def _open_about(self) -> None:
        # Drills in from Settings, so the card it came from goes away with it.
        self._close_modal()
        self._open_modal(
            AboutCard(
                updater.inventory(),
                updater.update_path(),
                updater.read_state(),
                on_apply=self._confirm_update,
                on_back=self._open_settings,
                compact=self._profile.compact,
            )
        )

    def _confirm_update(self, ids: list[str], labels: list[str]) -> None:
        self._close_modal()
        note = "Installs on reboot, takes a few minutes"
        if len(labels) > 1:
            # Names go in the body, an unwrapped title would stretch the card.
            title = f"Update {len(labels)} components?"
            note = f"{', '.join(labels)}. {note}"
        else:
            title = f"Update {labels[0]}?"
        self._open_modal(
            message_card(
                title,
                note,
                [
                    ("Cancel", "", self._close_modal),
                    ("Update", "danger", lambda: self._apply_update(ids)),
                ],
            )
        )

    def _apply_update(self, ids: list[str]) -> None:
        self._close_modal()
        self._flush_pending_persist()
        self._open_modal(message_card("Starting the update", "", []))
        # Painted first: arming surveys apt and then reboots, all of it blocking.
        QtCore.QTimer.singleShot(_PAINT_MS, lambda: self._arm_update(ids))

    def _arm_update(self, ids: list[str]) -> None:
        try:
            updater.request_apply(*ids)
        except Exception as exc:  # noqa: BLE001 surface the failure, the box stays up
            log.error("update apply failed: %s", exc)
            self._show_message("Update failed", str(exc))

    def _on_backlight(self, pct: int) -> bool:
        """Live during drag: the panel itself is the feedback."""
        if self._backlight is None:
            return False
        if not self._backlight.set_percent(pct):
            return False
        self._backlight_pct = int(pct)
        self._backlight_persist.start()
        return True

    def _persist_backlight(self) -> None:
        if self._backlight_pct is not None:
            self.settings.set_backlight(self._backlight_pct)

    def _apply_histogram(self, enabled: bool) -> None:
        self._histogram_on = bool(enabled)
        self.engine.set_stats_output(self._histogram_on)
        self.viewfinder_area.set_histogram_enabled(self._histogram_on)
        self.settings.set_histogram(self._histogram_on)
        self._refresh_monitor_chip()
        log.info("histogram overlay %s", "on" if enabled else "off")

    def _apply_network(self, enabled: bool) -> None:
        self._close_modal()
        try:
            network.set_enabled(enabled)
        except Exception as exc:  # noqa: BLE001
            log.error("network toggle failed: %s", exc)
            self._show_message("Network toggle failed", str(exc))
            return
        log.info("networking %s", "enabled" if enabled else "disabled")

    def _shutdown(self) -> None:
        # No confirmation by design: power-cycle-heavy bench tool, save click.
        self._flush_pending_persist()
        try:
            poweroff()
        except Exception as exc:  # noqa: BLE001
            log.error("poweroff failed: %s", exc)
            self._show_message("Shutdown failed", str(exc))

    # lifecycle
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refit_timer.start()
        self._switch_cover.on_resize()
        # Camera's blocking start hides behind the boot cover, so kick it off at fullscreen.
        if self._boot_cover is not None and self._boot_cover.on_resize():
            QtCore.QTimer.singleShot(0, self._start_engine)

    @Slot()
    def _on_boot_revealed(self) -> None:
        self._boot_cover.deleteLater()
        self._boot_cover = None
        QtCore.QTimer.singleShot(0, self._start_engine)

    def _on_screen_added(self, screen) -> None:
        self._blank_for_switch()
        screen.geometryChanged.connect(lambda _g: self._resync_fullscreen())
        self._resync_fullscreen()

    def _on_screen_removed(self, _screen) -> None:
        self._blank_for_switch()
        self._resync_fullscreen()

    def _blank_for_switch(self) -> None:
        if self._boot_cover is None:  # boot cover already blanks everything
            self._switch_cover.blank()

    def _on_display_changed(self, screen) -> None:
        """Output settled: swap profile and fullscreen. Lores refit follows the resize."""
        g = screen.geometry()
        key = (screen.name(), g.width(), g.height())
        if key == self._display_key:
            return
        self._display_key = key
        log.info("display: %s %dx%d", screen.name(), g.width(), g.height())
        profile = profile_for_screen(screen)
        if profile != self._profile:
            self._apply_profile(profile)
        self._resync_fullscreen()
        QtCore.QTimer.singleShot(0, self._check_chrome_fit)

    def _check_chrome_fit(self) -> None:
        """Chrome wider than the screen clips silently, so say so loudly."""
        screen = self.screen()
        if screen is None:
            return
        hint = self.minimumSizeHint().width()
        if hint > screen.geometry().width():
            log.warning(
                "chrome minimum width %d px exceeds the %d px screen, right edge will clip",
                hint,
                screen.geometry().width(),
            )

    def _apply_profile(self, profile: UiProfile) -> None:
        """Re-skin live for a new display class (monitor or touch panel)."""
        self._profile = profile
        self.setStyleSheet(build_stylesheet(profile))
        px = profile.icon_px
        for btn in self._chrome_btns:
            btn.setIconSize(QtCore.QSize(px, px))
        # Accents re-tint only on flips, clear the latch so icons rebuild at the new size.
        # Chip widths are re-pinned by _populate_static via _reserve_chip_widths.
        for btn in self._sheet_buttons.values():
            btn.setProperty("manual", None)
        self.settings_btn.setIcon(icons.icon("settings", px))
        self.shutdown_btn.setIcon(icons.icon("power_settings_new", px, "#d98b80"))
        self._apply_row_metrics()
        for sheet in self._sheets.values():
            sheet.apply_profile(profile)
        if self._open_sheet is not None:
            self._position_sheet(self._open_sheet)
        self.status.set_compact(profile.compact)
        self.viewfinder_area.apply_profile(profile)
        # A monitor already shows the full cluster in the strip.
        if not profile.compact:
            self.viewfinder_area.set_stats_overlay(False)
        self._populate_static()
        self._refresh_monitor_chip()
        self._update_status()

    def _refit_lores(self) -> None:
        if not self._engine_started:
            return
        try:
            if self.engine.refit_lores(self.viewfinder_area.lores_size()):
                log.info("lores stream refit to %dx%d", *self.engine.size)
        except Exception as exc:  # noqa: BLE001
            log.error("lores refit failed: %s", exc)

    def _resync_fullscreen(self) -> None:
        # Deferred so Qt finishes updating its QScreen state first.
        QtCore.QTimer.singleShot(0, self._apply_fullscreen)

    def _apply_fullscreen(self) -> None:
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        if screen is None:
            return
        g = screen.geometry()
        if self._boot_cover is not None:
            self._boot_cover.sync_geometry(g)
        if self.isFullScreen() and abs(self.width() - g.width()) <= 1:
            self._switch_cover.arm_lift()
            return
        log.info(
            "fullscreen resync: window=%dx%d screen=%dx%d",
            self.width(),
            self.height(),
            g.width(),
            g.height(),
        )
        # showFullScreen no-ops while Qt believes fullscreen, drop to normal first.
        self.showNormal()
        self.showFullScreen()

    def _start_engine(self) -> None:
        if self._engine_started:
            return
        self._engine_started = True
        if self.engine.picam2 is None or self.engine.current_mode is None:
            return
        # Boot lores size was an estimate. Camera has not started, so refitting is free.
        self._refit_lores()
        try:
            self.engine.start()
        except Exception as exc:  # noqa: BLE001
            log.error("camera start failed: %s", exc)

    # No quit affordance by design: exiting a kiosk drops to a blank tty.
    def closeEvent(self, event) -> None:
        try:
            self.engine.stop()
        finally:
            super().closeEvent(event)
