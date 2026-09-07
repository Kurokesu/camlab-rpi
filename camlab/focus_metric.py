# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Focus readout from ISP's CDAF grid."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from .qt import QtCore, Signal

log = logging.getLogger(__name__)

_CENTER_CELLS = 2
# Ref-count tag for the shared stats output.
_OWNER = "focus"


@dataclass(frozen=True)
class FocusSample:
    """Current sharpness."""

    raw: float | None = None  # center figure of merit
    # Scaled by the running peak cell, so the whole map dims as focus is lost.
    heat: np.ndarray | None = None


def center_score(grid, cells: int = _CENTER_CELLS) -> float:
    """Mean figure of merit over the center ``cells`` square of the grid."""
    lo = (grid.shape[0] - cells) // 2
    return float(grid[lo : lo + cells, lo : lo + cells].mean())


class FocusSampler(QtCore.QObject):
    """Focus metrics from the shared stats blob, polled by whoever shows them."""

    sample = Signal(object)  # FocusSample

    def __init__(self, engine, parent: QtCore.QObject | None = None):
        super().__init__(parent)
        self._engine = engine
        self._last = FocusSample()
        self._cell_peak = 0.0
        self._logged = False
        self._owners: set[str] = set()

    @property
    def sampling(self) -> bool:
        """True while a readout wants samples, which is when poll() is worth calling."""
        return bool(self._owners)

    def set_sampling(self, enabled: bool, owner: str) -> None:
        """Ref-counted like stats blob: samples while any owner has it enabled."""
        was = self.sampling
        if enabled:
            if owner in self._owners:
                return
            self._owners.add(owner)
            # A readout switching on scores against the current scene.
            self._rewind()
        else:
            self._owners.discard(owner)
        if self.sampling != was:
            self._engine.set_stats_output(self.sampling, owner=_OWNER)

    def _rewind(self) -> None:
        """Reset peak hold, so a scene is not scored against the previous one."""
        self._cell_peak = 0.0
        self._last = FocusSample()

    def poll(self) -> None:
        """Read the latest grid and emit. Driven by the caller's telemetry tick."""
        md = self._engine.telemetry.metadata
        grid = self._engine.cdaf_focus(md)
        self._describe_once(md, grid)
        if grid is None:
            # libcamera can skip the blob on a frame, hold rather than blink.
            self.sample.emit(self._last)
            return
        self._cell_peak = max(self._cell_peak, float(grid.max()))
        self._last = FocusSample(
            raw=center_score(grid),
            heat=grid / self._cell_peak if self._cell_peak > 0 else None,
        )
        self.sample.emit(self._last)

    def _describe_once(self, md: dict, grid) -> None:
        """Log what the ISP offers, once, so a shifted blob layout is visible."""
        if self._logged or "PispStatsOutput" not in md:
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
