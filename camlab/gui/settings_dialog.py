# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Settings card: app-level system options, one row per setting.

Brightness and display choice when a built-in display is present, auto white
balance algorithm, networking toggle and a drill-in to About. Apply only acts
on changed rows. Brightness, display and auto WB apply live.
"""

from __future__ import annotations

from collections.abc import Callable

from .. import network
from ..display import BACKLIGHT_FLOOR_PCT, apply_output_layout
from ..drm import has_dsi_display
from ..qt import Qt, QtWidgets
from ..settings import AwbMode, DisplayMode, SettingsStore
from . import icons
from .control_sheet import JumpSlider
from .widgets import SegmentedSelector, hline

_ICON_PX = 20


class SettingsCard(QtWidgets.QFrame):
    def __init__(
        self,
        settings: SettingsStore,
        backlight_pct: int | None,
        on_apply_network: Callable[[bool], None],
        on_backlight: Callable[[int], bool],
        on_grey_world: Callable[[bool], None],
        on_cancel: Callable[[], None],
        on_about: Callable[[], None] | None = None,
    ):
        super().__init__()
        self.setObjectName("modalCard")
        self.setMinimumWidth(420)
        self._settings = settings
        self._on_apply_network = on_apply_network
        self._on_backlight = on_backlight
        self._on_grey_world = on_grey_world
        self._on_cancel = on_cancel
        self._net_initial = network.is_enabled()

        title = QtWidgets.QLabel("Settings")
        title.setObjectName("modalTitle")

        form = QtWidgets.QFormLayout()

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

        if has_dsi_display():
            disp_label = QtWidgets.QLabel()
            disp_label.setPixmap(icons.pixmap("monitor", _ICON_PX, "#8a909b"))
            disp_row = QtWidgets.QHBoxLayout()
            disp_row.setSpacing(8)
            self.display_sel = SegmentedSelector()
            self.display_sel.set_options(
                [
                    ("External", DisplayMode.EXTERNAL),
                    ("Built-in", DisplayMode.BUILTIN),
                    ("Both", DisplayMode.BOTH),
                ],
                current=settings.get_display(),
            )
            self.display_sel.changed.connect(self._on_display_changed)
            disp_row.addWidget(disp_label)
            disp_row.addWidget(self.display_sel, 1)
            form.addRow("Display:", disp_row)

        awb_label = QtWidgets.QLabel()
        awb_label.setPixmap(icons.pixmap("wb_sunny", _ICON_PX, "#8a909b"))
        awb_row = QtWidgets.QHBoxLayout()
        awb_row.setSpacing(8)
        self.awb_sel = SegmentedSelector()
        self.awb_sel.set_options(
            [("Grey world", AwbMode.GREY), ("libcamera", AwbMode.LIBCAMERA)],
            current=settings.get_awb(),
        )
        self.awb_sel.changed.connect(self._on_awb_changed)
        awb_row.addWidget(awb_label)
        awb_row.addWidget(self.awb_sel, 1)
        form.addRow("Auto WB:", awb_row)

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
        form.addRow("Network:", net_row)

        note = QtWidgets.QLabel('"Off" boots faster, Ethernet stays up until reboot')
        note.setObjectName("dialogNote")
        note.setWordWrap(True)
        note.setMaximumWidth(400)
        form.addRow(note)

        # None hides row, for a caller with no About card to drill into.
        if on_about is not None:
            self.about_btn = QtWidgets.QPushButton("About")
            self.about_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            self.about_btn.clicked.connect(on_about)
            # Spans the row: opens a card rather than setting a value.
            form.addRow(self.about_btn)

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

    def _on_display_changed(self) -> None:
        mode = self.display_sel.current_value()
        # Persist first, hotplug settle re-applies from the store
        self._settings.set_display(mode)
        apply_output_layout(mode)

    def _on_awb_changed(self) -> None:
        mode = self.awb_sel.current_value()
        self._settings.set_awb(mode)
        self._on_grey_world(mode is AwbMode.GREY)

    def _refresh_apply(self) -> None:
        """Apply is live only when a selection changed."""
        self.apply_btn.setEnabled(bool(self.net_sel.current_value()) != self._net_initial)

    def _apply(self) -> None:
        net = bool(self.net_sel.current_value())
        if net != self._net_initial:
            self._on_apply_network(net)  # closes the modal itself
        else:
            self._on_cancel()
