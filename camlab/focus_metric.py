# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Focus readout from ISP's CDAF grid."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from .qt import QtCore, Signal

log = logging.getLogger(__name__)

_POLL_MS = 100
_CENTER_CELLS = 2
# Ref-count tag for the shared stats output.
_OWNER = "focus"
# Frame-to-frame scatter is 0.7% at fixed exposure, under 2% is noise.
_TREND_EPS = 0.02


@dataclass(frozen=True)
class FocusSample:
    """Current sharpness. Score is the fraction of the running peak."""

    score: float | None = None
    raw: float | None = None  # center FoM before peak scaling
    trend: int = 0  # +1 sharpening, -1 softening, 0 steady
    # Scaled by the running peak cell, so the whole map dims as focus is lost.
    heat: np.ndarray | None = None


def center_score(grid, cells: int = _CENTER_CELLS) -> float:
    """Mean figure of merit over the center ``cells`` square of the grid."""
    lo = (grid.shape[0] - cells) // 2
    return float(grid[lo : lo + cells, lo : lo + cells].mean())


class FocusSampler(QtCore.QObject):
    """Poll focus metrics while a readout is showing them."""

    sample = Signal(object)  # FocusSample

    def __init__(self, engine, parent: QtCore.QObject | None = None):
        super().__init__(parent)
        self._engine = engine
        self._last = FocusSample()
        self._peak = 0.0
        self._cell_peak = 0.0
        self._logged = False
        # Explicit, so an unbalanced start would not leave stats blob switched on.
        self._running = False
        self._owners: set[str] = set()
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(_POLL_MS)
        self._timer.timeout.connect(self._poll)

    def set_sampling(self, enabled: bool, owner: str) -> None:
        """Ref-counted like stats blob: polls while any owner has it enabled."""
        if not enabled:
            self._owners.discard(owner)
            if not self._owners:
                self.stop()
            return
        if owner in self._owners:
            return
        self._owners.add(owner)
        # A readout switching on scores against the current scene.
        self._rewind()
        self.start()

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._rewind()
        self._engine.set_stats_output(True, owner=_OWNER)
        self._timer.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        self._timer.stop()
        self._engine.set_stats_output(False, owner=_OWNER)

    def _rewind(self) -> None:
        """Reset peak hold, so a scene is not scored against the previous one."""
        self._peak = 0.0
        self._cell_peak = 0.0
        self._last = FocusSample()

    def _poll(self) -> None:
        md = self._engine.telemetry.metadata
        grid = self._engine.cdaf_focus(md)
        self._describe_once(md, grid)
        if grid is None:
            # libcamera can skip the blob on a frame, hold rather than blink.
            self.sample.emit(self._last)
            return
        raw = center_score(grid)
        self._peak = max(self._peak, raw)
        self._cell_peak = max(self._cell_peak, float(grid.max()))
        previous = self._last.raw
        self._last = FocusSample(
            score=raw / self._peak if self._peak > 0 else None,
            raw=raw,
            trend=_trend(previous, raw),
            heat=grid / self._cell_peak if self._cell_peak > 0 else None,
        )
        self.sample.emit(self._last)

    def _describe_once(self, md: dict, grid) -> None:
        """Log what the ISP offers, once, so a shifted blob layout is visible."""
        if self._logged or not md:
            return
        self._logged = True
        if grid is None:
            log.warning("no CDAF grid in stats, focus readout unavailable")
            return
        log.info(
            "CDAF grid %dx%d, cells %g to %g, FocusFoM %s",
            *grid.shape,
            float(grid.min()),
            float(grid.max()),
            md.get("FocusFoM"),
        )


def _trend(previous: float | None, raw: float) -> int:
    """Which way focus is going, which is what says which way to turn."""
    if previous is None or previous <= 0:
        return 0
    change = (raw - previous) / previous
    if change > _TREND_EPS:
        return 1
    if change < -_TREND_EPS:
        return -1
    return 0
