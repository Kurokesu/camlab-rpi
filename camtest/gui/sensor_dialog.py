"""Sensor selection card - pick sensor + CSI port, then Apply & Reboot.

Rendered inside a ModalOverlay (not a separate window, which a Cage kiosk renders
unreliably). Changing the sensor overlay requires a reboot (dtoverlay is read at
boot), so the primary action is explicit about it. Port (cam0/cam1) is the
secondary, rig-level setting.
"""

from __future__ import annotations

from collections.abc import Callable

from ..qt import QtWidgets
from ..sensors import SensorRegistry


class SensorCard(QtWidgets.QFrame):
    def __init__(self, registry: SensorRegistry, current_name: str | None,
                 current_port: str, on_apply: Callable[[str, str], None],
                 on_cancel: Callable[[], None]):
        super().__init__()
        self.setObjectName("modalCard")
        self.setMinimumWidth(420)
        self._on_apply = on_apply

        title = QtWidgets.QLabel("Select sensor")
        title.setObjectName("modalTitle")

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
        note.setObjectName("modalText")
        note.setWordWrap(True)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(on_cancel)
        apply_btn = QtWidgets.QPushButton("Apply && Reboot")
        apply_btn.setObjectName("danger")
        apply_btn.clicked.connect(self._apply)
        buttons.addWidget(cancel_btn)
        buttons.addWidget(apply_btn)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(14)
        lay.addWidget(title)
        lay.addLayout(form)
        lay.addWidget(note)
        lay.addLayout(buttons)

    def _apply(self) -> None:
        self._on_apply(self.sensor_combo.currentText(), self.port_combo.currentText())
