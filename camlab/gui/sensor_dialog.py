"""Sensor selection card - pick sensor + CSI port (+ color/mono), then shut down.

Rendered inside a ModalDialog (not a separate window, which a Cage kiosk renders
unreliably). Changing the sensor overlay requires a reboot (dtoverlay is read at
boot), but a swap means physically changing the sensor, which needs the box
powered off anyway. So apply writes config.txt and shuts down: the operator
swaps the sensor while it is off, then powers back on to the new overlay. Port
(cam0/cam1) is a per-rig setting. The Color/Mono variant is a per-rig choice too,
shown only for sensors that ship in both and cannot auto-detect
(Sensor.mono_capable). The selected sensor's free-form note (Sensor.notes) is
shown to the right of the title.
"""

from __future__ import annotations

from collections.abc import Callable

from ..qt import Qt, QtWidgets
from ..sensors import SensorRegistry
from .widgets import SegmentedSelector, hline


class SensorCard(QtWidgets.QFrame):
    def __init__(self, registry: SensorRegistry, current_name: str | None,
                 current_port: str, current_mono: bool,
                 on_apply: Callable[[str, str, bool], None],
                 on_cancel: Callable[[], None]):
        super().__init__()
        self.setObjectName("modalCard")
        self.setMinimumWidth(420)
        self._registry = registry
        self._on_apply = on_apply
        # Remember the initially-selected sensor + its variant so re-selecting it
        # restores the choice (other sensors default to color).
        self._init_name = current_name
        self._init_mono = bool(current_mono)

        title = QtWidgets.QLabel("Select sensor")
        title.setObjectName("modalTitle")

        # Selected sensor's note (Sensor.notes), to the right of the title so it
        # does not split the selector rows.
        self.notes_lbl = QtWidgets.QLabel()
        self.notes_lbl.setObjectName("modalText")
        self.notes_lbl.setAlignment(Qt.AlignmentFlag.AlignRight
                                    | Qt.AlignmentFlag.AlignVCenter)

        header = QtWidgets.QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.notes_lbl)

        form = QtWidgets.QFormLayout()
        self.sensor_sel = SegmentedSelector()
        self.sensor_sel.set_options([(name, name) for name in registry.names],
                                    current=current_name)
        self.sensor_sel.changed.connect(self._on_sensor_changed)

        self.port_sel = SegmentedSelector()
        port = current_port if current_port in ("cam0", "cam1") else "cam1"
        self.port_sel.set_options([("cam0", "cam0"), ("cam1", "cam1")], current=port)
        self.port_sel.changed.connect(self._refresh_apply)
        self._init_port = port

        self.variant_lbl = QtWidgets.QLabel("Variant:")
        self.variant_sel = SegmentedSelector()
        self.variant_sel.changed.connect(self._refresh_apply)

        form.addRow("Sensor:", self.sensor_sel)
        form.addRow("CSI port:", self.port_sel)
        form.addRow(self.variant_lbl, self.variant_sel)

        self._rebuild_variant(current_name, self._init_mono)
        self._update_notes(current_name)

        buttons = QtWidgets.QHBoxLayout()
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(on_cancel)
        self.apply_btn = QtWidgets.QPushButton("Apply && Shutdown")
        self.apply_btn.setObjectName("danger")
        self.apply_btn.clicked.connect(self._apply)
        # Apply here powers off, so a bare Enter must not trigger it: Cancel is the
        # primary target (what Enter hits before tabbing to a button). Applying
        # needs a deliberate Tab-to-Apply then Enter, or a click.
        self.primary_button = cancel_btn
        buttons.addWidget(cancel_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.apply_btn)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(14)
        lay.addLayout(header)
        lay.addLayout(form)
        lay.addWidget(hline())
        lay.addLayout(buttons)

        self._refresh_apply()

    def _on_sensor_changed(self) -> None:
        name = self.sensor_sel.current_value()
        # Restore the variant only for the sensor we opened on. Others start color.
        mono = self._init_mono if name == self._init_name else False
        self._rebuild_variant(name, mono)
        self._update_notes(name)
        self._refresh_apply()

    def _refresh_apply(self) -> None:
        """Apply is live only when a selection changed."""
        selected = (self.sensor_sel.current_value(),
                    self.port_sel.current_value(),
                    bool(self.variant_sel.current_value()))
        initial = (self._init_name, self._init_port, self._init_mono)
        self.apply_btn.setEnabled(selected != initial)

    def _update_notes(self, sensor_name: str | None) -> None:
        sensor = self._registry.by_name(sensor_name) if sensor_name else None
        self.notes_lbl.setText(sensor.notes if sensor and sensor.notes else "")
        self.notes_lbl.setVisible(bool(self.notes_lbl.text()))

    def _rebuild_variant(self, sensor_name: str | None, mono: bool) -> None:
        sensor = self._registry.by_name(sensor_name) if sensor_name else None
        capable = bool(sensor and sensor.mono_capable)
        if capable:
            self.variant_sel.set_options([("Color", False), ("Mono", True)],
                                         current=bool(mono), enabled=True)
        else:  # color-only or auto-detecting: nothing to choose
            self.variant_sel.set_options([("Color", False)], current=False,
                                         enabled=False)
        self.variant_lbl.setVisible(capable)
        self.variant_sel.setVisible(capable)

    def _apply(self) -> None:
        self._on_apply(self.sensor_sel.current_value(),
                       self.port_sel.current_value(),
                       bool(self.variant_sel.current_value()))
