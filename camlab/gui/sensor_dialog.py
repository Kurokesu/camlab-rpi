# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Sensor selection card: pick sensor, CSI port and touch display, then shut down.

Rendered inside ModalOverlay (Cage kiosk renders separate windows unreliably).
Apply writes config.txt blocks and powers off for rewire. Pi 5 locks display row to auto-detected.
"""

from __future__ import annotations

from collections.abc import Callable

from ..dsi_panels import PanelRegistry
from ..qt import Qt, QtWidgets
from ..sensors import SensorRegistry
from .widgets import SegmentedSelector, hline


class SensorCard(QtWidgets.QFrame):
    def __init__(
        self,
        registry: SensorRegistry,
        panels: PanelRegistry,
        current_name: str | None,
        current_port: str,
        current_mono: bool,
        current_display: str | None,
        display_locked: bool,
        locked_ports: set[str],
        offcat_port: str | None,
        on_apply: Callable[[str, str, bool, str | None], None],
        on_cancel: Callable[[], None],
    ):
        super().__init__()
        self.setObjectName("modalCard")
        self.setMinimumWidth(420)
        self._registry = registry
        self._on_apply = on_apply
        self._display_locked = bool(display_locked)
        self._locked_ports = set(locked_ports)
        # Off-catalogue display blocks kept as-is on apply, port they claim locks while selected.
        self._offcat_name = (
            current_display
            if current_display is not None and panels.by_name(current_display) is None
            else None
        )
        self._offcat_port = offcat_port
        # Remember the initially-selected sensor + its variant so re-selecting it
        # restores the choice (other sensors default to color).
        self._init_name = current_name
        self._init_mono = bool(current_mono)

        title = QtWidgets.QLabel("Select sensor")
        title.setObjectName("modalTitle")

        # Selected sensor note (Sensor.notes), right of title.
        self.notes_lbl = QtWidgets.QLabel()
        self.notes_lbl.setObjectName("modalText")
        self.notes_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        header = QtWidgets.QHBoxLayout()
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.notes_lbl)

        form = QtWidgets.QFormLayout()
        self.sensor_sel = SegmentedSelector()
        self.sensor_sel.set_options([(name, name) for name in registry.names], current=current_name)
        self.sensor_sel.changed.connect(self._on_sensor_changed)

        self.display_sel = SegmentedSelector()
        if self._display_locked:
            # Pi 5 firmware owns the panel, nothing to choose here.
            self.display_sel.set_options([("Auto-detected", None)], current=None, enabled=False)
        else:
            options: list[tuple[str, str | None]] = [("None", None)]
            options += [(name, name) for name in panels.names]
            if self._offcat_name is not None:
                # Keep off-catalogue overlays selectable so Apply does not clobber them.
                options.append((self._offcat_name, self._offcat_name))
            self.display_sel.set_options(options, current=current_display)
        self.display_sel.changed.connect(self._on_wiring_changed)
        self._init_display = current_display

        self.port_sel = SegmentedSelector()
        port = current_port if current_port in ("cam0", "cam1") else "cam1"
        self._port_locks_applied = self._port_locks()
        self.port_sel.set_options(
            [("cam0", "cam0"), ("cam1", "cam1")],
            current=port,
            disabled_values=self._port_locks_applied,
        )
        self.port_sel.changed.connect(self._on_wiring_changed)
        # Persisted port not selector state: forced shift off locked port registers pending change.
        self._init_port = port

        self.wiring_note = QtWidgets.QLabel()
        self.wiring_note.setObjectName("dialogNote")
        self.wiring_note.setWordWrap(True)

        self.variant_lbl = QtWidgets.QLabel("Variant:")
        self.variant_sel = SegmentedSelector()
        self.variant_sel.changed.connect(self._refresh_apply)

        form.addRow("Sensor:", self.sensor_sel)
        form.addRow(self.variant_lbl, self.variant_sel)
        form.addRow("CSI port:", self.port_sel)
        form.addRow("Touch display:", self.display_sel)
        form.addRow(self.wiring_note)

        self._rebuild_variant(current_name, self._init_mono)
        self._update_notes(current_name)
        self._sync_wiring_note()

        hint = QtWidgets.QLabel("Power off and unplug RPi before rewiring")
        hint.setObjectName("modalHint")

        buttons = QtWidgets.QHBoxLayout()
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(on_cancel)
        self.apply_btn = QtWidgets.QPushButton("Apply && Shutdown")
        self.apply_btn.setObjectName("danger")
        self.apply_btn.clicked.connect(self._apply)
        # Apply powers off. Bare Enter must not trigger it: Cancel is primary.
        # Apply needs Tab-to-Apply then Enter or click.
        self.primary_button = cancel_btn
        buttons.addWidget(cancel_btn)
        buttons.addStretch(1)
        buttons.addWidget(hint)
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

    def _port_locks(self) -> set[str]:
        """Ports the camera cannot take under the current display selection."""
        locks = set(self._locked_ports)
        if self._offcat_port is not None and self.display_sel.current_value() == self._offcat_name:
            locks.add(self._offcat_port)
        return locks

    def _on_wiring_changed(self) -> None:
        locks = self._port_locks()
        if locks != self._port_locks_applied:
            self._port_locks_applied = locks
            self.port_sel.set_options(
                [("cam0", "cam0"), ("cam1", "cam1")],
                current=self.port_sel.current_value(),
                disabled_values=locks,
            )
        self._sync_wiring_note()
        self._refresh_apply()

    def _sync_wiring_note(self) -> None:
        """State the wiring map, derived from the camera port."""
        if self._display_locked:
            ports = ", ".join(sorted(self._locked_ports))
            text = f"{ports} is used by the auto-detected touch display" if ports else ""
        elif self.display_sel.current_value() is not None:
            cam, disp = ("0", "1") if self.port_sel.current_value() == "cam0" else ("1", "0")
            text = f"Camera on CAM/DISP{cam}, touch display on CAM/DISP{disp}"
        else:
            text = ""
        self.wiring_note.setText(text)
        self.wiring_note.setVisible(bool(text))

    def _refresh_apply(self) -> None:
        """Apply is live only when a selection changed."""
        selected = (
            self.sensor_sel.current_value(),
            self.port_sel.current_value(),
            bool(self.variant_sel.current_value()),
            self.display_sel.current_value(),
        )
        initial = (self._init_name, self._init_port, self._init_mono, self._init_display)
        self.apply_btn.setEnabled(selected != initial)

    def _update_notes(self, sensor_name: str | None) -> None:
        sensor = self._registry.by_name(sensor_name) if sensor_name else None
        self.notes_lbl.setText(sensor.notes if sensor and sensor.notes else "")
        self.notes_lbl.setVisible(bool(self.notes_lbl.text()))

    def _rebuild_variant(self, sensor_name: str | None, mono: bool) -> None:
        sensor = self._registry.by_name(sensor_name) if sensor_name else None
        capable = bool(sensor and sensor.mono_capable)
        if capable:
            self.variant_sel.set_options(
                [("Color", False), ("Mono", True)], current=bool(mono), enabled=True
            )
        else:  # color-only or auto-detecting: nothing to choose
            self.variant_sel.set_options([("Color", False)], current=False, enabled=False)
        self.variant_lbl.setVisible(capable)
        self.variant_sel.setVisible(capable)

    def _apply(self) -> None:
        self._on_apply(
            self.sensor_sel.current_value(),
            self.port_sel.current_value(),
            bool(self.variant_sel.current_value()),
            self.display_sel.current_value(),
        )
