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
from .widgets import SegmentedSelector


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
        self.sensor_sel = SegmentedSelector()
        self.sensor_sel.set_options([(name, name) for name in registry.names],
                                    current=current_name)

        self.port_sel = SegmentedSelector()
        self.port_sel.set_options([("cam0", "cam0"), ("cam1", "cam1")],
                                  current=current_port if current_port in ("cam0", "cam1") else "cam0")

        form.addRow("Sensor:", self.sensor_sel)
        form.addRow("CSI port:", self.port_sel)

        note = QtWidgets.QLabel(
            "Applying rewrites dtoverlay in config.txt")
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
        self._on_apply(self.sensor_sel.current_value(), self.port_sel.current_value())
