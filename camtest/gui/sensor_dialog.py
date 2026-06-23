"""Sensor selection dialog - pick sensor + CSI port, then Apply & Reboot.

Changing the sensor overlay requires a reboot (dtoverlay is read at boot), so the
primary action is explicit about it. Port (cam0/cam1) is the secondary, rig-level
setting.
"""

from __future__ import annotations

from ..sensors import SensorRegistry
from ..qt import QtWidgets


class SensorDialog(QtWidgets.QDialog):
    def __init__(self, registry: SensorRegistry, current_name: str | None,
                 current_port: str = "cam0", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select sensor")
        self.setModal(True)

        form = QtWidgets.QFormLayout()

        self.sensor_combo = QtWidgets.QComboBox()
        for name in registry.names:
            self.sensor_combo.addItem(name)
        if current_name and current_name in registry.names:
            self.sensor_combo.setCurrentText(current_name)

        self.port_combo = QtWidgets.QComboBox()
        self.port_combo.addItems(["cam0", "cam1"])
        self.port_combo.setCurrentText(current_port if current_port in ("cam0", "cam1") else "cam0")

        form.addRow("Sensor:", self.sensor_combo)
        form.addRow("CSI port:", self.port_combo)

        note = QtWidgets.QLabel(
            "Applying rewrites the dtoverlay in config.txt and reboots so the new "
            "sensor is loaded at boot.")
        note.setWordWrap(True)
        note.setObjectName("dialogNote")

        buttons = QtWidgets.QDialogButtonBox()
        self.apply_btn = buttons.addButton("Apply && Reboot", QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.addButton(QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(form)
        lay.addWidget(note)
        lay.addWidget(buttons)

    @property
    def selected_sensor(self) -> str:
        return self.sensor_combo.currentText()

    @property
    def selected_port(self) -> str:
        return self.port_combo.currentText()
