# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Settings card: app-level system options, one row per setting.

Networking toggle, histogram overlay and panel brightness on touch display.
Apply only acts on changed rows. Brightness applies live while dragging.
"""

from __future__ import annotations

from collections.abc import Callable

from .. import network
from ..display import BACKLIGHT_FLOOR_PCT
from ..qt import Qt, QtWidgets
from . import icons
from .control_sheet import JumpSlider
from .widgets import SegmentedSelector, hline

_ICON_PX = 20


class SettingsCard(QtWidgets.QFrame):
    def __init__(
        self,
        histogram_on: bool,
        backlight_pct: int | None,
        on_apply_network: Callable[[bool], None],
        on_apply_histogram: Callable[[bool], None],
        on_backlight: Callable[[int], bool],
        on_cancel: Callable[[], None],
        on_updates: Callable[[], None] | None = None,
        updates_pending: int = 0,
    ):
        super().__init__()
        self.setObjectName("modalCard")
        self.setMinimumWidth(420)
        self._on_apply_network = on_apply_network
        self._on_apply_histogram = on_apply_histogram
        self._on_backlight = on_backlight
        self._on_cancel = on_cancel
        self._net_initial = network.is_enabled()
        self._hist_initial = bool(histogram_on)

        title = QtWidgets.QLabel("Settings")
        title.setObjectName("modalTitle")

        form = QtWidgets.QFormLayout()
        net_label = QtWidgets.QLabel()
        net_label.setPixmap(
            icons.pixmap("lan", _ICON_PX, "#98c379" if self._net_initial else "#8a909b")
        )
        net_row = QtWidgets.QHBoxLayout()
        net_row.setSpacing(8)
        self.net_sel = SegmentedSelector()
        self.net_sel.set_options([("On", True), ("Off", False)], current=self._net_initial)
        self.net_sel.changed.connect(self._refresh_apply)
        net_row.addWidget(net_label)
        net_row.addWidget(self.net_sel, 1)
        form.addRow("Networking:", net_row)

        note = QtWidgets.QLabel(
            '"Off" makes camlab boot faster. Ethernet stays connected until next boot.'
        )
        note.setObjectName("dialogNote")
        note.setWordWrap(True)
        note.setMaximumWidth(400)

        form.addRow(note)

        hist_label = QtWidgets.QLabel()
        hist_label.setPixmap(icons.pixmap("bar_chart", _ICON_PX, "#8a909b"))
        hist_row = QtWidgets.QHBoxLayout()
        hist_row.setSpacing(8)
        self.hist_sel = SegmentedSelector()
        self.hist_sel.set_options([("On", True), ("Off", False)], current=self._hist_initial)
        self.hist_sel.changed.connect(self._refresh_apply)
        hist_row.addWidget(hist_label)
        hist_row.addWidget(self.hist_sel, 1)
        form.addRow("Histogram:", hist_row)

        # None hides row: no backlight device or panel is not active display.
        if backlight_pct is not None:
            bl_label = QtWidgets.QLabel()
            bl_label.setPixmap(icons.pixmap("brightness_6", _ICON_PX, "#8a909b"))
            bl_row = QtWidgets.QHBoxLayout()
            bl_row.setSpacing(8)
            self.backlight_slider = JumpSlider(Qt.Orientation.Horizontal)
            self.backlight_slider.setRange(BACKLIGHT_FLOOR_PCT, 100)
            self.backlight_slider.setValue(int(backlight_pct))
            self.backlight_slider.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            self.backlight_slider.valueChanged.connect(self._on_backlight_moved)
            self.backlight_lbl = QtWidgets.QLabel(f"{int(backlight_pct)}%")
            self.backlight_lbl.setMinimumWidth(44)
            bl_row.addWidget(bl_label)
            bl_row.addWidget(self.backlight_slider, 1)
            bl_row.addWidget(self.backlight_lbl)
            form.addRow("Brightness:", bl_row)

        # None hides the row, for a caller with no updater to drill into.
        if on_updates is not None:
            upd_label = QtWidgets.QLabel()
            upd_label.setPixmap(icons.pixmap("update", _ICON_PX, "#8a909b"))
            upd_row = QtWidgets.QHBoxLayout()
            upd_row.setSpacing(8)
            self.updates_btn = QtWidgets.QPushButton(
                f"{updates_pending} available" if updates_pending else "Check"
            )
            self.updates_btn.clicked.connect(on_updates)
            upd_row.addWidget(upd_label)
            upd_row.addWidget(self.updates_btn)
            upd_row.addStretch(1)
            form.addRow("Updates:", upd_row)

        buttons = QtWidgets.QHBoxLayout()
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(on_cancel)
        self.apply_btn = QtWidgets.QPushButton("Apply")
        self.apply_btn.clicked.connect(self._apply)
        self.apply_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        # Networking off cuts reachability. Bare Enter must not trigger Apply: Cancel is primary.
        self.primary_button = cancel_btn
        buttons.addWidget(cancel_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.apply_btn)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(14)
        lay.addWidget(title)
        lay.addLayout(form)
        lay.addWidget(hline())
        lay.addLayout(buttons)

        self._refresh_apply()

    def _on_backlight_moved(self, value: int) -> None:
        if self._on_backlight(int(value)):
            self.backlight_lbl.setText(f"{value}%")
        else:  # write failed, stop pretending the slider works
            self.backlight_slider.setEnabled(False)
            self.backlight_lbl.setText("n/a")

    def _refresh_apply(self) -> None:
        """Apply is live only when a selection changed."""
        net_changed = bool(self.net_sel.current_value()) != self._net_initial
        hist_changed = bool(self.hist_sel.current_value()) != self._hist_initial
        self.apply_btn.setEnabled(net_changed or hist_changed)

    def _apply(self) -> None:
        hist = bool(self.hist_sel.current_value())
        if hist != self._hist_initial:
            self._on_apply_histogram(hist)
        net = bool(self.net_sel.current_value())
        if net != self._net_initial:
            self._on_apply_network(net)  # closes the modal itself
        else:
            self._on_cancel()
