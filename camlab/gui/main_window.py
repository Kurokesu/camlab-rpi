# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""MainWindow - fullscreen bench UI: viewfinder + status strip + controls + log."""

from __future__ import annotations

import logging

from .. import network
from ..camera import CameraEngine
from ..config_manager import ConfigManager, poweroff
from ..integrity import IntegrityMonitor, LogClassifier, StderrCapture
from ..modes import mode_for
from ..qt import Qt, QtCore, QtGui, QtWidgets, Signal, Slot
from ..sensors import SensorRegistry
from ..settings import SettingsStore
from ..stats import RpiStats
from . import icons
from .control_sheet import ControlSheet, MonitorSheet, fmt_ct, fmt_exposure, fmt_gain
from .log_panel import LogPanel
from .mode_dialog import ModeCard
from .overlay import ModalOverlay, message_card
from .sensor_dialog import SensorCard
from .settings_dialog import SettingsCard
from .status_strip import StatusStrip
from .style import build_stylesheet
from .viewfinder_area import ViewfinderArea
from .widgets import repolish, vline

log = logging.getLogger(__name__)

# On-screen icon size for the control-bar buttons.
_ICON_PX = 21

# Amber = "not showing the plain picture" (manual control, assist overlay).
_ACCENT_ON = "#e5c07b"
_ACCENT_OFF = "#d7dae0"


class MainWindow(QtWidgets.QMainWindow):
    first_frame = Signal(float)

    # (chip label, icon glyph, metadata key, value formatter) per camera control.
    _CTRL_SPEC = {
        "exposure_us": ("Exp", "shutter_speed", "ExposureTime", fmt_exposure),
        "gain": ("Gain", "iso", "AnalogueGain", fmt_gain),
        "colour_temp": ("WB", "wb_sunny", "ColourTemperature", fmt_ct),
    }

    def __init__(self, engine: CameraEngine, registry: SensorRegistry,
                 config: ConfigManager, capture: StderrCapture,
                 classifier: LogClassifier, settings: SettingsStore):
        super().__init__()
        self.engine = engine
        self.registry = registry
        self.config = config
        self.capture = capture
        self.settings = settings
        self.monitor = IntegrityMonitor(classifier)
        self._overlay: ModalOverlay | None = None
        self._boot_cover: QtWidgets.QWidget | None = None
        self._engine_started = False

        self.setWindowTitle("camlab")
        self.setStyleSheet(build_stylesheet())

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        # Focus sink: clicking empty chrome parks focus here, so no button
        # shows a stray highlight until the operator tabs to one.
        central.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Split bars: live facts on top, controls on the bottom, viewfinder between.
        self.status = StatusStrip()
        root.addWidget(self.status)

        # viewfinder (live GL, frosted in-shader while a modal is up)
        self.viewfinder_area = ViewfinderArea(engine)
        self.viewfinder_area.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                                        QtWidgets.QSizePolicy.Policy.Expanding)
        root.addWidget(self.viewfinder_area, 1)

        # Control sheets dock over viewfinder's bottom edge as plain translucent
        # widgets (the in-scene viewfinder composites under them). Exposure
        # and gain span decades, hence log sliders.
        self._sheets: dict[str, QtWidgets.QWidget] = {
            "exposure_us": ControlSheet("Exposure", fmt_exposure, log_scale=True,
                                        parent=self),
            "gain": ControlSheet("Gain", fmt_gain, log_scale=True, integer=False,
                                 parent=self),
            "colour_temp": ControlSheet("White balance", fmt_ct,
                                        parent=self),
            "monitor": MonitorSheet(parent=self),
        }
        self._open_sheet: str | None = None
        for sheet in self._sheets.values():
            sheet.setVisible(False)
        for key in self._CTRL_SPEC:
            self._sheets[key].changed.connect(
                lambda v, k=key: self._on_control_changed(k, v))
        self._sheets["monitor"].changed.connect(self._on_monitor_changed)
        # Keep the open sheet glued to viewfinder's bottom edge on resize
        # (e.g. opening the log panel shrinks the viewfinder).
        self.viewfinder_area.installEventFilter(self)

        # Sensor/Mode are merged status+chooser buttons. Shutdown is fenced behind
        # a divider on the right so it is never a mis-click from Log.
        controls = QtWidgets.QFrame()
        controls.setObjectName("controls")
        crow = QtWidgets.QHBoxLayout(controls)
        crow.setContentsMargins(10, 6, 10, 6)
        crow.setSpacing(8)
        self.sensor_btn = QtWidgets.QPushButton()
        self.sensor_btn.clicked.connect(self._choose_sensor)
        self.mode_btn = QtWidgets.QPushButton()
        self.mode_btn.clicked.connect(self._choose_mode)
        self.mode_btn.setEnabled(bool(self.engine.modes))
        # Camera-control chips: live value on the button, amber accent when
        # manual, click opens a floating sheet (clicking another one switches).
        self._ctrl_buttons: dict[str, QtWidgets.QPushButton] = {
            key: QtWidgets.QPushButton(f" {label}")
            for key, (label, _glyph, _md, _fmt) in self._CTRL_SPEC.items()
        }
        self.monitor_btn = QtWidgets.QPushButton(
            icons.icon("stroke_partial", _ICON_PX), " Monitor")
        # Sheet-opening chips: camera controls plus the monitor-assist toggle.
        self._sheet_buttons = dict(self._ctrl_buttons, monitor=self.monitor_btn)
        for key, btn in self._sheet_buttons.items():
            btn.setCheckable(True)
            # Left-anchored so icon and label hold still inside the ratcheted
            # width while the value's tail grows and shrinks.
            btn.setObjectName("chip")
            btn.clicked.connect(lambda _=False, k=key: self._toggle_sheet(k))
        self.settings_btn = QtWidgets.QPushButton(
            icons.icon("settings", _ICON_PX), " Settings")
        self.settings_btn.clicked.connect(self._open_settings)
        self.log_btn = QtWidgets.QPushButton(icons.icon("terminal", _ICON_PX), " Log")
        self.log_btn.setCheckable(True)
        self.log_btn.toggled.connect(self._toggle_log)
        self.shutdown_btn = QtWidgets.QPushButton(
            icons.icon("power_settings_new", _ICON_PX, "#d98b80"), " Shutdown")
        self.shutdown_btn.setObjectName("danger")
        self.shutdown_btn.clicked.connect(self._shutdown)

        # QPushButton clamps the icon to a small default, so set the size explicitly.
        # TabFocus (not the default StrongFocus): these are reachable by Tab but a
        # mouse click does not leave a lingering focus ring on them.
        for btn in (self.sensor_btn, self.mode_btn, *self._ctrl_buttons.values(),
                    self.monitor_btn, self.settings_btn, self.log_btn,
                    self.shutdown_btn):
            btn.setIconSize(QtCore.QSize(_ICON_PX, _ICON_PX))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)

        # Sensor and Mode read as one selection group, so no divider between.
        crow.addWidget(self.sensor_btn)
        crow.addWidget(self.mode_btn)
        crow.addSpacing(6)
        crow.addWidget(vline())
        crow.addSpacing(6)
        for btn in self._ctrl_buttons.values():
            crow.addWidget(btn)
        crow.addWidget(self.monitor_btn)
        crow.addStretch(1)
        crow.addWidget(self.settings_btn)
        crow.addWidget(self.log_btn)
        crow.addSpacing(6)
        crow.addWidget(vline())
        crow.addSpacing(6)
        crow.addWidget(self.shutdown_btn)
        root.addWidget(controls)

        # Log panel (collapsed by default) sits below the controls. Both stretch
        # 1, so opening the log shrinks the viewfinder to fit.
        self.log_panel = LogPanel(classifier)
        self.log_panel.setVisible(False)
        root.addWidget(self.log_panel, 1)

        self._wire()
        self._populate_static()
        # Histogram overlay (beta easter egg), persisted app-wide, default off.
        self._histogram_on = settings.get_histogram()
        if self._histogram_on:
            self.engine.set_stats_output(True)
            self.viewfinder_area.set_histogram_enabled(True)
        # Start with focus on the inert sink so nothing is highlighted until Tab.
        central.setFocus(Qt.FocusReason.OtherFocusReason)

        # Window shortcuts fire regardless of which child holds focus, so they
        # cover both the main screen and the in-window modal overlay (a plain
        # QWidget with no default-button routing of its own). _on_escape is
        # tiered (modal -> log -> shutdown), _on_return clicks the focused button.
        esc = QtGui.QShortcut(QtGui.QKeySequence(Qt.Key.Key_Escape), self)
        esc.setContext(Qt.ShortcutContext.WindowShortcut)
        esc.activated.connect(self._on_escape)
        for seq in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            sc = QtGui.QShortcut(QtGui.QKeySequence(seq), self)
            sc.setContext(Qt.ShortcutContext.WindowShortcut)
            sc.activated.connect(self._on_return)

        # Telemetry at 10 Hz: about the fastest a changing number stays readable.
        self._status_timer = QtCore.QTimer(self)
        self._status_timer.setInterval(100)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start()

        # Board health at 1 Hz: load percentages are deltas, and a second is a
        # meaningful averaging window (10 Hz would read as noise).
        self._rpi_stats = RpiStats()
        self._rpi_timer = QtCore.QTimer(self)
        self._rpi_timer.setInterval(1000)
        self._rpi_timer.timeout.connect(
            lambda: self.status.set_rpi_stats(self._rpi_stats.sample()))
        self._rpi_timer.start()

        # Debounce control persistence so a slider drag is one write, not one
        # per tick.
        self._persist_timer = QtCore.QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.setInterval(500)
        self._persist_timer.timeout.connect(self._persist_controls)

        # Qt commits its first surface at the layout's size hint before the
        # compositor's fullscreen configure lands, so the window flashes small
        # on boot. A black screen-sized cover hides that until the window is
        # laid out fullscreen.
        self._boot_cover = QtWidgets.QWidget(central)
        self._boot_cover.setStyleSheet("background: #000;")
        screen = QtWidgets.QApplication.primaryScreen()
        sg = screen.geometry() if screen is not None else central.rect()
        self._boot_cover.setGeometry(0, 0, sg.width(), sg.height())
        self._boot_cover.raise_()
        self._reveal_timer = QtCore.QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.setInterval(250)
        self._reveal_timer.timeout.connect(self._reveal)
        self._fallback_tries = 0
        QtCore.QTimer.singleShot(3000, self._reveal_fallback)

    # wiring
    def _wire(self) -> None:
        self.capture.line_received.connect(self.log_panel.append_line)
        self.capture.line_received.connect(self.monitor.feed)
        self.monitor.stats_changed.connect(self.status.update_integrity)
        self.first_frame.connect(self._on_first_frame)
        self.engine.on_first_frame(lambda boot_time: self.first_frame.emit(boot_time))

    @staticmethod
    def _is_mono(sensor, options: list[str]) -> bool:
        """True if the sensor's mono overlay param is active in config.txt."""
        return bool(sensor and sensor.mono_option and sensor.mono_option in options)

    def _populate_static(self) -> None:
        self._refresh_sensor_status()
        self._refresh_mode_status()
        self._refresh_control_buttons()

    def _refresh_sensor_status(self) -> None:
        """Update the merged Sensor chip: selection text plus a detection glyph
        (green check when the detected module matches, amber when it differs, red
        when nothing is detected)."""
        cur = self.config.get_current()
        sensor = self.registry.by_overlay(cur["overlay"]) if cur["overlay"] else None
        name = sensor.name if sensor else (cur["overlay"] or "unknown")
        variant = ", mono" if self._is_mono(sensor, cur["options"]) else ""
        self.sensor_btn.setText(f" Sensor: {name} ({cur['port']}{variant})")

        detected = self.engine.info.model if self.engine.info is not None else None
        overlay = cur["overlay"]
        if not detected:
            glyph, color, tip = "error", "#e06c75", "No camera detected by libcamera."
        elif overlay and detected.lower() == overlay.lower():
            glyph, color, tip = "check_circle", "#98c379", f"Detected {detected} (matches selection)."
        elif overlay:
            glyph, color, tip = ("warning", "#e5c07b",
                                 f"Detected {detected}, selection is {overlay}.")
        else:
            glyph, color, tip = "photo_camera", "#aeb4bf", f"Detected {detected}."
        self.sensor_btn.setIcon(icons.icon(glyph, _ICON_PX, color))
        self.sensor_btn.setToolTip(tip)

    def _refresh_mode_status(self) -> None:
        """Update the merged Mode chip with the active rpicam-style mode string."""
        m = self.engine.sensor_mode
        if m and m.get("format") and m.get("size"):
            w, h = m["size"]
            self.mode_btn.setText(f" Mode: {m['format']} {w}x{h}")
        else:
            self.mode_btn.setText(" Mode: --")
        self.mode_btn.setIcon(icons.icon("tune", _ICON_PX))

    def _refresh_control_buttons(self) -> None:
        """Show a control chip only when the camera offers that control (mono
        sensor drops WB, no camera at all drops all three)."""
        ranges = self.engine.control_ranges()
        for key, btn in self._ctrl_buttons.items():
            btn.setVisible(key in ranges)
        # Monitor shaders draw on the live stream, so any camera qualifies.
        self.monitor_btn.setVisible(self.viewfinder_area.has_camera)

    # slots
    @Slot(float)
    def _on_first_frame(self, boot_time: float) -> None:
        self.status.set_boot_time(boot_time)
        log.info("first frame at boot time=%.1fs", boot_time)

    def _update_status(self) -> None:
        # One snapshot read: #frame, fps and metadata come from the same frame
        # (camera thread publishes them as one reference).
        t = self.engine.telemetry
        md = t.metadata or {}
        self.status.set_telemetry(t.frame,
                                  t.fps if t.fps > 0 else None,
                                  md.get("ExposureTime"),
                                  md.get("AnalogueGain"),
                                  md.get("DigitalGain"))
        # SensorTemperature is not offered by every sensor (None keeps the
        # last reading).
        self.status.set_temperature(md.get("SensorTemperature"))
        # ISP histogram rides the same 10 Hz tick. The engine latches it off
        # any frame carrying stats, so this read never goes stale even when
        # the newest frame has no blob (libcamera skips some above 30 fps).
        if self._histogram_on and self.engine.latest_histogram is not None:
            self.viewfinder_area.update_histogram(self.engine.latest_histogram)
        # Control chips carry live values too, and the open sheet tracks its
        # value while in auto.
        st = self.engine.control_state
        for key, (label, glyph, md_key, fmt) in self._CTRL_SPEC.items():
            value = md.get(md_key)
            text = f" {label} {fmt(value)}" if value is not None else f" {label} --"
            btn = self._ctrl_buttons[key]
            if btn.text() != text:
                btn.setText(text)
                # Ratchet width so a chip never shrinks (e.g. metadata gap during
                # mode reconfigure) and its right neighbours hold still.
                btn.setMinimumWidth(max(btn.minimumWidth(), btn.sizeHint().width()))
            self._set_chip_accent(btn, glyph, getattr(st, key) is not None)
            if key == self._open_sheet:
                self._sheets[key].set_live(value)

    @staticmethod
    def _set_chip_accent(btn: QtWidgets.QPushButton, glyph: str,
                         active: bool) -> None:
        """Amber accent on/off. Re-polish (and tint the icon) only on a flip."""
        if btn.property("manual") == active:
            return
        btn.setProperty("manual", active)
        btn.setIcon(icons.icon(glyph, _ICON_PX,
                               _ACCENT_ON if active else _ACCENT_OFF))
        repolish(btn)

    def _toggle_log(self, checked: bool) -> None:
        self.log_panel.setVisible(checked)
        # The button is how you close it again, so make the open state read as a
        # pressed toggle (QSS :checked) and relabel it accordingly.
        if checked:
            self.log_btn.setIcon(icons.icon("close", _ICON_PX))
            self.log_btn.setText(" Close log")
        else:
            self.log_btn.setIcon(icons.icon("terminal", _ICON_PX))
            self.log_btn.setText(" Log")

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
        sheet.setGeometry(origin.x(), origin.y() + pa.height() - h,
                          pa.width(), h)

    def eventFilter(self, obj, event) -> bool:
        if (obj is self.viewfinder_area and event.type() == QtCore.QEvent.Type.Resize
                and self._open_sheet is not None):
            self._position_sheet(self._open_sheet)
        return super().eventFilter(obj, event)

    def _close_sheet(self) -> None:
        self._open_sheet = None
        for sheet in self._sheets.values():
            sheet.setVisible(False)
        for btn in self._sheet_buttons.values():
            btn.setChecked(False)
        self.centralWidget().setFocus(Qt.FocusReason.OtherFocusReason)

    def _seed_sheet(self, key: str) -> None:
        """Range + state from the engine (silent, no changed emission)."""
        sheet = self._sheets[key]
        rng = self.engine.control_ranges().get(key)
        if rng:
            sheet.set_range(*rng)
        sheet.set_state(getattr(self.engine.control_state, key))

    def _on_monitor_changed(self, peaking: bool, zebra: bool,
                            threshold: float) -> None:
        self.viewfinder_area.set_assists(peaking, zebra, threshold)
        # Amber chip while any assist draws on the viewfinder, same accent as
        # a manual camera control.
        self._set_chip_accent(self.monitor_btn, "stroke_partial",
                              peaking or zebra)

    def _on_control_changed(self, key: str, value) -> None:
        st = self.engine.set_control_state(**{key: value})
        # Engine clamps, so reflect what was actually set while manual.
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
        # Activate the focused button. Inside a modal, fall back to the card's
        # primary button so Enter works even before tabbing onto a button. Outside
        # a modal, no-op when focus is on the inert sink (no stray clicks).
        focused = QtWidgets.QApplication.focusWidget()
        if isinstance(focused, QtWidgets.QPushButton) and focused.isEnabled():
            focused.click()
            return
        if self._overlay is not None:
            primary = getattr(self._overlay.card, "primary_button", None)
            if primary is not None and primary.isEnabled():
                primary.click()

    def _on_escape(self) -> None:
        # Tiered: close the frontmost layer if one is open (modal, then sheet,
        # then log), otherwise it is the kill switch - immediate poweroff, like
        # the Shutdown button (no confirm by design on this power-cycle tool).
        # Called both by the overlay (modal up) and the window Escape shortcut.
        if self._modal_active:
            self._close_modal()
        elif self._open_sheet is not None:
            self._close_sheet()
        elif self.log_btn.isChecked():
            self.log_btn.setChecked(False)
        else:
            self._shutdown()

    # in-window modals (a Cage kiosk renders separate top-level dialogs as a
    # tiny unusable artifact, so everything is drawn over the main surface).
    def _open_modal(self, card) -> None:
        if self._modal_active:
            return  # one modal at a time
        # A sheet under the dimmed backdrop would stay interactive-looking, so
        # close it (state lives in the engine, nothing is lost).
        self._close_sheet()
        # Frost the live viewfinder and leave its area undimmed, so it reads at
        # full strength while the surrounding chrome dims for focus. Without a
        # camera this hides the placeholder text instead (it cannot blur).
        self.viewfinder_area.set_frost(True)
        clear = None
        if self.viewfinder_area.has_camera:
            clear = self.viewfinder_area.geometry()
        # The overlay traps Tab and swallows backdrop clicks. Enter/Escape come
        # from MainWindow's window shortcuts (they fire regardless of focus).
        self._overlay = ModalOverlay(self.centralWidget(), card, clear_rect=clear)

    def _close_modal(self) -> None:
        if self._overlay is not None:
            self._overlay.dismiss()
            self._overlay = None
        self.viewfinder_area.set_frost(False)
        # Park focus back on the inert sink so no control is left highlighted
        # (Qt would otherwise restore focus to whatever had it before the modal).
        self.centralWidget().setFocus(Qt.FocusReason.OtherFocusReason)

    def _show_message(self, title: str, message: str) -> None:
        self._open_modal(message_card(
            title, message, [("OK", "", self._close_modal)]))

    def _choose_mode(self) -> None:
        if not self.engine.modes:
            self._show_message("No modes", "No selectable sensor modes were enumerated.")
            return
        # Viewfinder area at open time sizes the new mode's lores (display) stream.
        self._mode_avail = self.viewfinder_area.lores_size()
        card = ModeCard(self.engine.modes, self.engine.current_mode,
                        self.engine.current_fps,
                        on_apply=self._apply_mode, on_cancel=self._close_modal)
        self._open_modal(card)

    def _apply_mode(self, size: tuple[int, int], bit_depth: int, fps: float) -> None:
        self._close_modal()
        mode = mode_for(self.engine.modes, tuple(size), int(bit_depth))
        if mode is None:  # re-validate at apply time
            self._show_message("Mode unavailable", "That mode is no longer available.")
            return
        avail = getattr(self, "_mode_avail", self.viewfinder_area.lores_size())
        try:
            self.engine.apply_mode(mode, float(fps), avail)
        except Exception as exc:
            log.exception("apply mode failed")
            self._show_message("Mode change failed", str(exc))
            return
        # Persist only after a successful reconfigure (never store an unrunnable
        # config). The key is the selected sensor's overlay token.
        overlay = self.config.get_current().get("overlay") or ""
        self.settings.set_mode(overlay, tuple(size), int(bit_depth), float(fps))
        self.monitor.reset()
        self._refresh_mode_status()
        # New mode may have re-clamped manual values (exposure vs new frame
        # duration), so persist the possibly adjusted state.
        self._persist_timer.start()

    def _choose_sensor(self) -> None:
        cur = self.config.get_current()
        sensor = self.registry.by_overlay(cur["overlay"]) if cur["overlay"] else None
        mono = self._is_mono(sensor, cur["options"])
        card = SensorCard(self.registry, sensor.name if sensor else None,
                          cur["port"], mono, on_apply=self._apply_sensor,
                          on_cancel=self._close_modal)
        self._open_modal(card)

    def _apply_sensor(self, sensor_name: str, port: str, mono: bool) -> None:
        self._close_modal()
        chosen = self.registry.by_name(sensor_name)
        if chosen is None:
            return
        options = list(chosen.options)
        if mono and chosen.mono_option and chosen.mono_option not in options:
            options.append(chosen.mono_option)
        # Flush before the config rewrite: _persist_controls keys by the
        # current overlay, which apply() is about to change.
        self._flush_pending_persist()
        try:
            self.config.apply(chosen.overlay, port, options)
        except Exception as exc:  # surface the failure, do not power off
            self._show_message("Apply failed", str(exc))
            return
        # A sensor swap needs the box off, so power down rather than reboot: the
        # operator swaps while it is off, then powers on to the new overlay.
        poweroff()

    def _open_settings(self) -> None:
        card = SettingsCard(histogram_on=self._histogram_on,
                            on_apply_network=self._apply_network,
                            on_apply_histogram=self._apply_histogram,
                            on_cancel=self._close_modal)
        self._open_modal(card)

    def _apply_histogram(self, enabled: bool) -> None:
        self._histogram_on = bool(enabled)
        self.engine.set_stats_output(self._histogram_on)
        self.viewfinder_area.set_histogram_enabled(self._histogram_on)
        self.settings.set_histogram(self._histogram_on)
        log.info("histogram overlay %s", "on" if enabled else "off")

    def _apply_network(self, enabled: bool) -> None:
        self._close_modal()
        try:
            network.set_enabled(enabled)
        except Exception as exc:
            log.error("network toggle failed: %s", exc)
            self._show_message("Network toggle failed", str(exc))
            return
        log.info("networking %s", "enabled" if enabled else "disabled")

    def _shutdown(self) -> None:
        # No confirmation by design: this is a power-cycle-heavy bench tool, so
        # the button powers off immediately to save a click.
        self._flush_pending_persist()
        try:
            poweroff()
        except Exception as exc:
            log.error("poweroff failed: %s", exc)
            self._show_message("Shutdown failed", str(exc))

    # lifecycle
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._boot_cover is None:
            return
        # Reaching fullscreen width once is not enough: early Wayland configure
        # churn can still commit a buffer at the pre-fullscreen size, flashing
        # chrome mid-screen. Reveal only after the size holds fullscreen for a
        # settle window (any resize restarts it). The camera starts as soon as
        # fullscreen is first reached, so its blocking start hides behind the
        # cover and the reveal shows chrome with video already flowing.
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        if screen is not None and self.width() >= screen.geometry().width() - 1:
            QtCore.QTimer.singleShot(0, self._start_engine)
            self._reveal_timer.start()
        else:
            self._reveal_timer.stop()

    def _reveal_fallback(self) -> None:
        # At cold boot the compositor can hold the first fullscreen configure
        # past this timer (display modeset in progress), and dropping the cover
        # then would bare the next pre-fullscreen buffer. Re-arm while the
        # window is not fullscreen yet, revealing unconditionally only after
        # ten tries so a non-kiosk session cannot stay covered forever.
        if self._boot_cover is None:
            return
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        fullscreen = (screen is not None
                      and self.width() >= screen.geometry().width() - 1)
        self._fallback_tries += 1
        if fullscreen or self._fallback_tries >= 10:
            self._reveal()
        else:
            QtCore.QTimer.singleShot(3000, self._reveal_fallback)

    def _reveal(self) -> None:
        if self._boot_cover is None:
            return
        self._boot_cover.hide()
        self._boot_cover.deleteLater()
        self._boot_cover = None
        QtCore.QTimer.singleShot(0, self._start_engine)

    def _start_engine(self) -> None:
        if self._engine_started:
            return
        self._engine_started = True
        if self.engine.picam2 is None or self.engine.current_mode is None:
            return
        try:
            self.engine.start()
        except Exception as exc:
            log.error("camera start failed: %s", exc)

    # No quit affordance by design: this is a kiosk. Exiting drops to a blank
    # tty, which an operator never wants. Stop it with `camlabctl stop`.
    def closeEvent(self, event) -> None:
        try:
            self.engine.stop()
        finally:
            super().closeEvent(event)
