"""MainWindow - fullscreen bench UI: preview + status strip + controls + log."""

from __future__ import annotations

import logging
import os

from ..camera import CameraEngine
from ..config_manager import ConfigManager
from ..integrity import IntegrityMonitor, LogClassifier, StderrCapture
from ..qt import Qt, QtWidgets, Signal, Slot
from ..sensors import SensorRegistry
from . import icons
from .log_panel import LogPanel
from .sensor_dialog import SensorDialog
from .status_strip import StatusStrip

log = logging.getLogger(__name__)

_STYLE = """
QWidget { background: #1b1d22; color: #d7dae0; font-size: 13px; }
QFrame#statusStrip { background: #23262d; border-top: 1px solid #2f333c; }
QLabel[class="chip"] { color: #aeb4bf; }
QLabel#integrity { font-weight: 600; padding: 2px 10px; border-radius: 4px; }
QLabel#integrity[state="ok"]  { color: #98c379; }
QLabel#integrity[state="warn"]{ color: #e5c07b; background: #3a3320; }
QLabel#integrity[state="bad"] { color: #ffffff; background: #b3402f; }
QPushButton { background: #2c303a; border: 1px solid #3a3f4b; border-radius: 5px;
              padding: 6px 12px; }
QPushButton:hover { background: #353b47; }
QPushButton#danger { border-color: #803126; }
QPushButton#danger:hover { background: #50211a; }
QCheckBox { color: #aeb4bf; spacing: 6px; }
QCheckBox::indicator { width: 15px; height: 15px; border: 1px solid #4a505c;
                       border-radius: 3px; background: #2c303a; }
QCheckBox::indicator:hover { border-color: #6a7180; }
QCheckBox::indicator:checked { border-color: #6a7180; }
QCheckBox::indicator:checked:hover { border-color: #808998; }
QPlainTextEdit#logView { background: #15171b; border: none; color: #c4c9d2; }
QLabel#logTitle { color: #8a909b; font-weight: 600; }
QLabel#dialogNote { color: #8a909b; }
"""


class MainWindow(QtWidgets.QMainWindow):
    first_frame = Signal(float)

    def __init__(self, engine: CameraEngine, registry: SensorRegistry,
                 config: ConfigManager, capture: StderrCapture,
                 classifier: LogClassifier, binding_label: str = ""):
        super().__init__()
        self.engine = engine
        self.registry = registry
        self.config = config
        self.capture = capture
        self.binding_label = binding_label
        self.monitor = IntegrityMonitor(classifier)

        self.setWindowTitle("camtest")
        self.setStyleSheet(_STYLE + self._checkbox_tick_style())

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # preview
        if engine.picam2 is not None:
            self.preview = engine.make_preview_widget()
        else:
            self.preview = QtWidgets.QLabel("No camera detected")
            self.preview.setAlignment(Qt.AlignCenter)
            self.preview.setStyleSheet("font-size: 22px; color: #e06c75;")
        self.preview.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                   QtWidgets.QSizePolicy.Expanding)
        root.addWidget(self.preview, 1)

        # status strip
        self.status = StatusStrip()
        root.addWidget(self.status)

        # controls
        controls = QtWidgets.QHBoxLayout()
        controls.setContentsMargins(10, 6, 10, 6)
        controls.setSpacing(8)
        self.sensor_btn = QtWidgets.QPushButton(icons.icon("photo_camera"), " Sensor...")
        self.sensor_btn.clicked.connect(self._choose_sensor)
        self.log_btn = QtWidgets.QPushButton(icons.icon("terminal"), " Log")
        self.log_btn.setCheckable(True)
        self.log_btn.toggled.connect(self._toggle_log)
        self.shutdown_btn = QtWidgets.QPushButton(
            icons.icon("power_settings_new", color="#d98b80"), " Shutdown")
        self.shutdown_btn.setObjectName("danger")
        self.shutdown_btn.clicked.connect(self._shutdown)

        controls.addWidget(self.sensor_btn)
        controls.addStretch(1)
        controls.addWidget(self.log_btn)
        controls.addWidget(self.shutdown_btn)
        root.addLayout(controls)

        # log panel (collapsed by default)
        self.log_panel = LogPanel(classifier)
        self.log_panel.setVisible(False)
        root.addWidget(self.log_panel, 1)

        self._wire()
        self._populate_static()

    @staticmethod
    def _checkbox_tick_style() -> str:
        # A neutral tick for the checked state (the blue fill clashed with the
        # palette). Rendered from the icon font to a PNG since Qt stylesheets
        # need an image url for sub-control glyphs.
        path = icons.cached_png("check", 13, "#cdd3dd")
        return f"QCheckBox::indicator:checked {{ image: url({path}); }}" if path else ""

    # wiring
    def _wire(self) -> None:
        self.capture.line_received.connect(self.log_panel.append_line)
        self.capture.line_received.connect(self.monitor.feed)
        self.monitor.stats_changed.connect(self.status.update_integrity)
        self.first_frame.connect(self._on_first_frame)
        self.engine.on_first_frame(lambda boottime: self.first_frame.emit(boottime))

    def _populate_static(self) -> None:
        cur = self.config.get_current()
        sensor = self.registry.by_overlay(cur["overlay"]) if cur["overlay"] else None
        name = sensor.name if sensor else (cur["overlay"] or "unknown")
        # The button is the single source of truth for the selected sensor + port.
        self.sensor_btn.setText(f"Sensor: {name} ({cur['port']})")
        detected = self.engine.info.model if self.engine.info is not None else None
        self.status.set_camera(detected, cur["overlay"])
        if self.engine.info is not None and self.engine.sensor_mode:
            m = self.engine.sensor_mode
            w, h = m["size"]
            self.status.set_mode(m["format"], f"{w}x{h}")

    # slots
    @Slot(float)
    def _on_first_frame(self, boottime: float) -> None:
        self.status.set_boot_time(boottime)
        log.info("first frame at boottime=%.1fs", boottime)

    def _toggle_log(self, checked: bool) -> None:
        self.log_panel.setVisible(checked)

    def _choose_sensor(self) -> None:
        cur = self.config.get_current()
        sensor = self.registry.by_overlay(cur["overlay"]) if cur["overlay"] else None
        dlg = SensorDialog(self.registry, sensor.name if sensor else None,
                           cur["port"], self)
        result = dlg.exec_() if hasattr(dlg, "exec_") else dlg.exec()
        if not result:
            return
        chosen = self.registry.by_name(dlg.selected_sensor)
        if chosen is None:
            return
        try:
            self.config.apply(chosen.overlay, dlg.selected_port, list(chosen.options))
        except Exception as exc:  # surface the failure, do not reboot
            QtWidgets.QMessageBox.critical(self, "Apply failed", str(exc))
            return
        if os.environ.get("CAMTEST_NO_REBOOT"):
            QtWidgets.QMessageBox.information(
                self, "Applied (reboot skipped)",
                f"config.txt updated: {chosen.overlay} on {dlg.selected_port}.\n"
                "CAMTEST_NO_REBOOT set - reboot manually to load it.")
            self._populate_static()
            return
        from ..config_manager import reboot
        reboot()

    def _shutdown(self) -> None:
        if QtWidgets.QMessageBox.question(
                self, "Shutdown", "Power off the device?") != QtWidgets.QMessageBox.Yes:
            return
        from ..config_manager import poweroff
        poweroff()

    # lifecycle
    # No quit affordance by design: this is a kiosk. Exiting drops to a blank
    # tty, which an operator never wants. Stop it with `camtestctl stop`.
    def closeEvent(self, event) -> None:
        try:
            self.engine.stop()
        finally:
            super().closeEvent(event)
