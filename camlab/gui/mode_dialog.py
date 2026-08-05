# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Sensor mode selection card: Resolution, Bit depth, FPS cascade.

Inline SegmentedSelectors (Cage misplaces dropdown popups). Selectors dependent:
resolution reconciles bit depth, upstream change rebuilds FPS row. Apply persists
only after MainWindow confirms reconfigure.
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
    def __init__(
        self,
        modes: list[SensorMode],
        current_mode: SensorMode | None,
        fps_current: float | None,
        fps_fixed: bool,
        on_apply: Callable[[tuple[int, int], int, float, bool], None],
        on_cancel: Callable[[], None],
        compact: bool = False,
    ):
        super().__init__()
        self.setObjectName("modalCard")
        self.setMinimumWidth(420)
        self._modes = modes
        self._on_apply = on_apply
        # Compact shortens labels so widest rows still fit one segment.
        self._compact = bool(compact)

        title = QtWidgets.QLabel("Sensor mode")
        title.setObjectName("modalTitle")

        self.res_sel = SegmentedSelector()
        self.depth_sel = SegmentedSelector()
        self.fps_sel = SegmentedSelector()
        self.fps_lock_sel = SegmentedSelector()

        init_size = tuple(current_mode.size) if current_mode else None
        sep = "x" if self._compact else " x "
        self.res_sel.set_options(
            [(f"{w}{sep}{h}", (w, h)) for (w, h) in resolutions(modes)], current=init_size
        )
        self._rebuild_depths(current_mode.bit_depth if current_mode else None)
        self._rebuild_fps(fps_current)
        self.fps_lock_sel.set_options(
            [("Fixed", True), ("Exposure driven", False)], current=bool(fps_fixed)
        )

        # Dirty check uses post-seed values (fps may snap to nearest option).
        self._initial = self._selection()

        # Connect after build so seeding stays silent.
        self.res_sel.changed.connect(self._on_res_changed)
        self.depth_sel.changed.connect(self._on_depth_changed)
        self.fps_sel.changed.connect(self._refresh_apply)
        self.fps_lock_sel.changed.connect(self._refresh_apply)

        form = QtWidgets.QFormLayout()
        form.addRow("Resolution:", self.res_sel)
        form.addRow("Bit depth:", self.depth_sel)
        form.addRow("FPS:", self.fps_sel)
        form.addRow("FPS lock:", self.fps_lock_sel)

        buttons = QtWidgets.QHBoxLayout()
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(on_cancel)
        self.apply_btn = QtWidgets.QPushButton("Apply")
        self.apply_btn.clicked.connect(self._apply)
        # Apply is safe (no reboot), so it is primary Enter target.
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
        return (
            self.res_sel.current_value(),
            self.depth_sel.current_value(),
            self.fps_sel.current_value(),
            self.fps_lock_sel.current_value(),
        )

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
        self.depth_sel.set_options([(f"{d}-bit", d) for d in depths], current=prefer_depth)

    def _rebuild_fps(self, prefer_fps: float | None) -> None:
        m = mode_for(self._modes, self.res_sel.current_value(), self.depth_sel.current_value())
        opts = fps_options(m.max_fps) if m else [30.0]
        unit = "" if self._compact else " fps"
        # Keep chosen rate when still offered, else nearest.
        self.fps_sel.set_options(
            [(f"{format_fps(o)}{unit}", o) for o in opts],
            current=nearest_fps_option(opts, prefer_fps),
            enabled=len(opts) > 1,
        )

    def _apply(self) -> None:
        self._on_apply(
            self.res_sel.current_value(),
            int(self.depth_sel.current_value()),
            float(self.fps_sel.current_value()),
            bool(self.fps_lock_sel.current_value()),
        )
