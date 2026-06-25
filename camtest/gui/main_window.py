"""MainWindow - fullscreen bench UI: preview + status strip + controls + log."""

from __future__ import annotations

import logging
import os

from ..camera import CameraEngine
from ..config_manager import ConfigManager
from ..integrity import IntegrityMonitor, LogClassifier, StderrCapture
from ..modes import mode_for
from ..qt import Qt, QtCore, QtGui, QtWidgets, Signal, Slot
from ..sensors import SensorRegistry
from ..settings import SettingsStore
from . import icons
from .log_panel import LogPanel
from .mode_dialog import ModeCard
from .overlay import ModalOverlay, message_card
from .preview_area import PreviewArea
from .sensor_dialog import SensorCard
from .status_strip import StatusStrip
from .widgets import vline

log = logging.getLogger(__name__)

# On-screen icon size for the control-bar buttons.
_ICON_PX = 21

_STYLE = """
QWidget { background: #1b1d22; color: #d7dae0; font-size: 13px; }
QFrame#statusStrip { background: #23262d; border-bottom: 1px solid #2f333c; }
QFrame#controls { background: #1b1d22; border-top: 1px solid #2f333c; }
QFrame#vsep, QFrame#hsep { background: #3a3f4b; }
QFrame#statusStrip QWidget { background: transparent; }
QLabel#telemetry { color: #c4c9d2; }
QLabel#bootInfo { color: #8a909b; }
QLabel#version { color: #8a909b; }
QLabel#errCount[sev="ok"], QLabel#warnCount[sev="ok"] { color: #98c379; }
QLabel#errCount[sev="alert"]  { color: #e06c75; font-weight: 600; }
QLabel#warnCount[sev="alert"] { color: #e5c07b; font-weight: 600; }
QPushButton { background: #2c303a; border: 1px solid #3a3f4b; border-radius: 5px;
              padding: 6px 12px; }
QPushButton:hover { background: #353b47; }
QPushButton:checked { background: #3d4858; border-color: #7f8aa0; color: #ffffff; }
QPushButton:focus { border-color: #7aa2f7; background: #353b47; outline: none; }
QPushButton#danger { border-color: #803126; }
QPushButton#danger:hover { background: #50211a; }
QPushButton#danger:focus { border-color: #e06c75; background: #50211a; outline: none; }
QPushButton#segment { background: #262a33; border: 1px solid #3a3f4b; border-radius: 0;
                      padding: 6px 14px; color: #c4c9d2; }
QPushButton#segment[pos="mid"], QPushButton#segment[pos="last"] { margin-left: -1px; }
QPushButton#segment[pos="first"] { border-top-left-radius: 6px; border-bottom-left-radius: 6px; }
QPushButton#segment[pos="last"] { border-top-right-radius: 6px; border-bottom-right-radius: 6px; }
QPushButton#segment[pos="only"] { border-radius: 6px; }
QPushButton#segment:hover { background: #2f3540; }
QPushButton#segment:checked { background: #3d4858; border-color: #7f8aa0; color: #ffffff; }
QPushButton#segment:checked:disabled { background: #2f3540; border-color: #4a505c; color: #aeb4bf; }
QPushButton#segment:focus { border-color: #7aa2f7; background: #2f3949; outline: none; }
QPushButton#segment:checked:focus { border-color: #9db8ff; background: #45526a; color: #ffffff; }
QCheckBox { color: #aeb4bf; spacing: 6px; }
QCheckBox::indicator { width: 20px; height: 20px; border: 1px solid #4a505c;
                       border-radius: 4px; background: #2c303a; }
QCheckBox::indicator:hover { border-color: #6a7180; }
QCheckBox::indicator:checked { border-color: #6a7180; }
QCheckBox::indicator:checked:hover { border-color: #808998; }
QPlainTextEdit#logView { background: #15171b; border: none; color: #c4c9d2; }
QLabel#logTitle { color: #8a909b; font-weight: 600; }
QLabel#dialogNote { color: #8a909b; }
QFrame#modalCard { background: #23262d; border: 1px solid #3a3f4b; border-radius: 8px; }
QFrame#modalCard QLabel { background: transparent; }
QLabel#modalTitle { font-size: 16px; font-weight: 600; color: #e8eaed; }
QLabel#modalText { color: #aeb4bf; }
"""


class MainWindow(QtWidgets.QMainWindow):
    first_frame = Signal(float)

    def __init__(self, engine: CameraEngine, registry: SensorRegistry,
                 config: ConfigManager, capture: StderrCapture,
                 classifier: LogClassifier, settings: SettingsStore,
                 display_max_fps: float, binding_label: str = ""):
        super().__init__()
        self.engine = engine
        self.registry = registry
        self.config = config
        self.capture = capture
        self.settings = settings
        self.display_max_fps = display_max_fps
        self.binding_label = binding_label
        self.monitor = IntegrityMonitor(classifier)
        self._overlay: ModalOverlay | None = None
        self._pending_card: QtWidgets.QWidget | None = None
        self._boot_cover: QtWidgets.QWidget | None = None
        self._engine_started = False

        self.setWindowTitle("camtest")
        self.setStyleSheet(_STYLE + self._checkbox_tick_style())

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        # Focus sink: clicking empty chrome (or boot) parks focus here instead of
        # on a control, so no button shows a stray highlight until the operator
        # actually tabs to one. ClickFocus also pulls focus off a button on click.
        central.setFocusPolicy(Qt.ClickFocus)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Split bars: live facts on top, controls on the bottom, preview between.
        self.status = StatusStrip()
        root.addWidget(self.status)

        # preview (live GL + frozen frost backdrop for modals)
        self.preview_area = PreviewArea(engine)
        self.preview_area.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                        QtWidgets.QSizePolicy.Expanding)
        root.addWidget(self.preview_area, 1)

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
        for btn in (self.sensor_btn, self.mode_btn, self.log_btn, self.shutdown_btn):
            btn.setIconSize(QtCore.QSize(_ICON_PX, _ICON_PX))
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.TabFocus)

        crow.addWidget(self.sensor_btn)
        crow.addSpacing(6)
        crow.addWidget(vline())
        crow.addSpacing(6)
        crow.addWidget(self.mode_btn)
        crow.addStretch(1)
        crow.addWidget(self.log_btn)
        crow.addSpacing(6)
        crow.addWidget(vline())
        crow.addSpacing(6)
        crow.addWidget(self.shutdown_btn)
        root.addWidget(controls)

        # Log panel (collapsed by default) sits below the controls, never abutting
        # the preview: the preview is a native EGL surface that overlaps adjacent
        # siblings. Both stretch 1, so opening the log shrinks the preview to fit.
        self.log_panel = LogPanel(classifier)
        self.log_panel.setVisible(False)
        root.addWidget(self.log_panel, 1)

        self._wire()
        self._populate_static()
        # Start with focus on the inert sink so nothing is highlighted until Tab.
        central.setFocus(Qt.OtherFocusReason)

        # Window shortcuts fire regardless of which child holds focus, so they
        # cover both the main screen and the in-window modal overlay (a plain
        # QWidget with no default-button routing of its own). _on_escape is
        # tiered (modal -> log -> shutdown); _on_return clicks the focused button.
        esc = QtWidgets.QShortcut(QtGui.QKeySequence(Qt.Key_Escape), self)
        esc.setContext(Qt.WindowShortcut)
        esc.activated.connect(self._on_escape)
        for seq in (Qt.Key_Return, Qt.Key_Enter):
            sc = QtWidgets.QShortcut(QtGui.QKeySequence(seq), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(self._on_return)

        # Sample telemetry at 10 Hz (100 ms): about the fastest a changing number
        # stays readable.
        self._status_timer = QtCore.QTimer(self)
        self._status_timer.setInterval(100)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start()

        # Qt commits its first surface at the layout's size hint before the
        # compositor's fullscreen configure lands, so the window flashes small on
        # boot. A black, screen-sized cover hides that (invisible on the black
        # background) until the window is laid out fullscreen, then it is dropped.
        self._boot_cover = QtWidgets.QWidget(central)
        self._boot_cover.setStyleSheet("background: #000;")
        screen = QtWidgets.QApplication.primaryScreen()
        sg = screen.geometry() if screen is not None else central.rect()
        self._boot_cover.setGeometry(0, 0, sg.width(), sg.height())
        self._boot_cover.raise_()
        QtCore.QTimer.singleShot(3000, self._reveal)  # fallback if never resized

    @staticmethod
    def _checkbox_tick_style() -> str:
        # Neutral tick for the checked state, rendered from the icon font to a PNG
        # since Qt stylesheets need an image url for sub-control glyphs.
        path = icons.cached_png("check", 17, "#cdd3dd")
        return f"QCheckBox::indicator:checked {{ image: url({path}); }}" if path else ""

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

    # slots
    @Slot(float)
    def _on_first_frame(self, boot_time: float) -> None:
        self.status.set_boot_time(boot_time)
        log.info("first frame at boot time=%.1fs", boot_time)

    def _update_status(self) -> None:
        # One snapshot read: #frame, fps and metadata are guaranteed to be from
        # the same frame (the camera thread publishes them as one reference).
        t = self.engine.telemetry
        md = t.metadata or {}
        # rpicam-style info text: #frame (fps) exp ag dg, refreshed at 10 Hz.
        self.status.set_telemetry(t.frame,
                                  t.fps if t.fps > 0 else None,
                                  md.get("ExposureTime"),
                                  md.get("AnalogueGain"),
                                  md.get("DigitalGain"))
        # SensorTemperature comes from the embedded-data parser and is not
        # offered by every sensor (None -> the last reading sticks).
        self.status.set_temperature(md.get("SensorTemperature"))

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

    @property
    def _modal_active(self) -> bool:
        """A modal is open or a snapshot is in flight to open one."""
        return self._overlay is not None or self._pending_card is not None

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
        # Tiered: close the frontmost layer if one is open (modal, then log),
        # otherwise it is the kill switch - immediate poweroff, like the Shutdown
        # button (no confirm by design on this power-cycle tool). Called both by
        # the overlay (modal up) and the window Escape shortcut (no modal).
        if self._modal_active:
            self._close_modal()
        elif self.log_btn.isChecked():
            self.log_btn.setChecked(False)
        else:
            self._shutdown()

    # in-window modals (a Cage kiosk renders separate top-level dialogs as a
    # tiny unusable artifact, so everything is drawn over the main surface).
    def _open_modal(self, card) -> None:
        if self._modal_active:
            return  # one modal at a time (a snapshot may be in flight)
        self._pending_card = card
        # Swap the live GL preview for a frosted still, then present the overlay
        # (a native window would otherwise stack above it). With no camera,
        # present right away over a full dim.
        if not self.preview_area.enter_freeze(self._present_pending):
            self._present_pending()

    def _present_pending(self) -> None:
        card = self._pending_card
        self._pending_card = None
        if card is None:
            return  # stale (closed before the snapshot landed)
        # Leave the frozen-preview area undimmed so the frost reads at full
        # strength. The surrounding chrome stays dimmed for focus.
        clear = self.preview_area.geometry() if self.preview_area.is_frozen else None
        # The overlay traps Tab and swallows backdrop clicks; Enter/Escape come
        # from MainWindow's window shortcuts (they fire regardless of focus).
        self._overlay = ModalOverlay(self.centralWidget(), card, clear_rect=clear)

    def _close_modal(self) -> None:
        if self._overlay is not None:
            self._overlay.dismiss()
            self._overlay = None
        self.preview_area.exit_freeze()
        # Park focus back on the inert sink so no control is left highlighted
        # (Qt would otherwise restore focus to whatever had it before the modal).
        self.centralWidget().setFocus(Qt.OtherFocusReason)

    def _show_message(self, title: str, message: str) -> None:
        self._open_modal(message_card(
            title, message, [("OK", "", self._close_modal)]))

    def _choose_mode(self) -> None:
        if not self.engine.modes:
            self._show_message("No modes", "No selectable sensor modes were enumerated.")
            return
        # Capture the live preview area BEFORE the overlay hides the GL widget. It
        # sizes the lores (display) stream of the new mode.
        self._mode_avail = self.preview_area.lores_size()
        card = ModeCard(self.engine.modes, self.engine.current_mode,
                        self.engine.current_fps, self.display_max_fps,
                        on_apply=self._apply_mode, on_cancel=self._close_modal)
        self._open_modal(card)

    def _apply_mode(self, size: tuple[int, int], bit_depth: int, fps: float) -> None:
        self._close_modal()
        mode = mode_for(self.engine.modes, tuple(size), int(bit_depth))
        if mode is None:  # re-validate at apply time
            self._show_message("Mode unavailable", "That mode is no longer available.")
            return
        avail = getattr(self, "_mode_avail", self.preview_area.lores_size())
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
        variant = " (mono)" if mono and chosen.mono_option else ""
        try:
            self.config.apply(chosen.overlay, port, options)
        except Exception as exc:  # surface the failure, do not power off
            self._show_message("Apply failed", str(exc))
            return
        if os.environ.get("CAMTEST_NO_REBOOT"):
            self._populate_static()
            self._show_message(
                "Applied (shutdown skipped)",
                f"config.txt updated: {chosen.overlay}{variant} on {port}.\n"
                "CAMTEST_NO_REBOOT set - power off and swap the sensor manually.")
            return
        # A sensor swap needs the box off, so power down rather than reboot: the
        # operator swaps while it is off, then powers on to the new overlay.
        from ..config_manager import poweroff
        poweroff()

    def _shutdown(self) -> None:
        # No confirmation by design: this is a power-cycle-heavy bench tool, so
        # the button powers off immediately to save a click.
        from ..config_manager import poweroff
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
        screen = self.screen() or QtWidgets.QApplication.primaryScreen()
        if screen is not None and self.width() >= screen.geometry().width() - 1:
            self._reveal()

    def _reveal(self) -> None:
        # Window is laid out at fullscreen: drop the boot cover to reveal the
        # ready chrome, then start the camera one tick later so that frame paints
        # first (the blocking start would otherwise freeze the just-revealed
        # chrome before it shows).
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
    # tty, which an operator never wants. Stop it with `camtestctl stop`.
    def closeEvent(self, event) -> None:
        try:
            self.engine.stop()
        finally:
            super().closeEvent(event)
