# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Sensor mode selection card - Resolution -> Bit depth -> FPS cascade.

Rendered inside a ModalDialog using inline SegmentedSelectors (not dropdowns,
whose popups misplace under the Cage kiosk). The three selectors are dependent:
changing the resolution reconciles the bit-depth row (keeping the current depth
if it still exists, else snapping to the deepest), and any change upstream
rebuilds the FPS row carrying the chosen rate over - kept when the new mode still
offers it, otherwise the nearest achievable rate (e.g. 60 -> 33.89 when 60 drops
out at 4K). The FPS row locks when a mode offers only one rate. The UI can
therefore only ever present a combination the hardware actually supports.

Applying does not persist directly: MainWindow applies it to the camera first and
only writes the persisted selection if the reconfigure succeeds.
"""

from __future__ import annotations

from collections.abc import Callable

from ..modes import (
    SensorMode,
    bit_depths_for,
    format_fps,
    fps_options,
    mode_for,
    nearest_fps_option,
    resolutions,
)
from ..qt import QtWidgets
from .widgets import SegmentedSelector, hline


class ModeCard(QtWidgets.QFrame):
    def __init__(self, modes: list[SensorMode], current_mode: SensorMode | None,
                 current_fps: float | None,
                 on_apply: Callable[[tuple[int, int], int, float], None],
                 on_cancel: Callable[[], None]):
        super().__init__()
        self.setObjectName("modalCard")
        self.setMinimumWidth(420)
        self._modes = modes
        self._on_apply = on_apply

        title = QtWidgets.QLabel("Sensor mode")
        title.setObjectName("modalTitle")

        self.res_sel = SegmentedSelector()
        self.depth_sel = SegmentedSelector()
        self.fps_sel = SegmentedSelector()

        init_size = tuple(current_mode.size) if current_mode else None
        self.res_sel.set_options(
            [(f"{w} x {h}", (w, h)) for (w, h) in resolutions(modes)],
            current=init_size)
        self._rebuild_depths(current_mode.bit_depth if current_mode else None)
        self._rebuild_fps(current_fps)

        # The dirty check compares against what the card actually shows after
        # seeding (current_fps may have been snapped to the nearest option).
        self._initial = self._selection()

        # Connect after the initial build so the seeding stays silent.
        self.res_sel.changed.connect(self._on_res_changed)
        self.depth_sel.changed.connect(self._on_depth_changed)
        self.fps_sel.changed.connect(self._refresh_apply)

        form = QtWidgets.QFormLayout()
        form.addRow("Resolution:", self.res_sel)
        form.addRow("Bit depth:", self.depth_sel)
        form.addRow("FPS:", self.fps_sel)

        buttons = QtWidgets.QHBoxLayout()
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(on_cancel)
        self.apply_btn = QtWidgets.QPushButton("Apply")
        self.apply_btn.clicked.connect(self._apply)
        # Apply is safe (no reboot), so it is the primary Enter target.
        self.primary_button = self.apply_btn
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

    def _selection(self) -> tuple:
        return (self.res_sel.current_value(),
                self.depth_sel.current_value(),
                self.fps_sel.current_value())

    def _refresh_apply(self) -> None:
        """Apply is live only when a selection changed."""
        self.apply_btn.setEnabled(self._selection() != self._initial)

    def _on_res_changed(self) -> None:
        prev_depth = self.depth_sel.current_value()
        prev_fps = self.fps_sel.current_value()
        self._rebuild_depths(prev_depth)
        self._rebuild_fps(prev_fps)
        self._refresh_apply()

    def _on_depth_changed(self) -> None:
        self._rebuild_fps(self.fps_sel.current_value())
        self._refresh_apply()

    def _rebuild_depths(self, prefer_depth: int | None) -> None:
        depths = bit_depths_for(self._modes, self.res_sel.current_value())  # deepest first
        self.depth_sel.set_options([(f"{d}-bit", d) for d in depths],
                                   current=prefer_depth)

    def _rebuild_fps(self, prefer_fps: float | None) -> None:
        m = mode_for(self._modes, self.res_sel.current_value(),
                     self.depth_sel.current_value())
        opts = fps_options(m.max_fps) if m else [30.0]
        # Carry the chosen rate over: keep it when still offered, else the nearest.
        self.fps_sel.set_options([(f"{format_fps(o)} fps", o) for o in opts],
                                 current=nearest_fps_option(opts, prefer_fps),
                                 enabled=len(opts) > 1)

    def _apply(self) -> None:
        self._on_apply(self.res_sel.current_value(),
                       int(self.depth_sel.current_value()),
                       float(self.fps_sel.current_value()))
