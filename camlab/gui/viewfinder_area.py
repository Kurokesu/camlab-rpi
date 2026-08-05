# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""ViewfinderArea hosts live viewfinder widget.

Owns main layout slot, exposes frost toggle for modals. Viewfinder renders in-scene,
overlays stack above. Corner overlays: histogram top-left, stats card top-right.
"""

from __future__ import annotations

import os

from ..qt import Qt, QtWidgets
from .histogram import MARGIN, HistogramOverlay
from .rpi_stats import RpiStatsCard


class ViewfinderArea(QtWidgets.QWidget):
    def __init__(self, engine, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._engine = engine
        self._frosted = False
        self._hist_enabled = False

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        if engine.picam2 is not None:
            self._live: QtWidgets.QWidget = engine.make_viewfinder()
            # Evaluation hook: boot with live frost on to judge the shader.
            if os.environ.get("CAMLAB_FROST"):
                self.set_frost(True)
        else:
            self._live = QtWidgets.QLabel("No camera detected")
            self._live.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._live.setStyleSheet("font-size: 22px; color: #e06c75;")
        lay.addWidget(self._live)

        self._histogram = HistogramOverlay(self)
        self._histogram.move(MARGIN, MARGIN)
        self._histogram.setVisible(False)

        self._stats_card = RpiStatsCard(self)
        self._stats_card.setVisible(False)
        self._stats_enabled = False
        self._stats_texts: dict[str, str | None] | None = None

    @property
    def has_camera(self) -> bool:
        return self._engine.picam2 is not None

    def lores_size(self) -> tuple[int, int]:
        """Current on-screen viewfinder size, for sizing a new mode's lores stream."""
        return self.width(), self.height()

    def set_frost(self, frosted: bool) -> None:
        """Blur live viewfinder in-shader.

        Without camera placeholder cannot blur, hides while frosted. Corner overlays
        hide too: frost signals attention on modal.
        """
        self._frosted = bool(frosted)
        if hasattr(self._live, "set_frosted"):
            self._live.set_frosted(frosted)
        else:
            self._live.setVisible(not frosted)
        self._sync_histogram_visible()
        self._sync_stats_visible()

    def set_histogram_enabled(self, enabled: bool) -> None:
        self._hist_enabled = bool(enabled) and self.has_camera
        if not self._hist_enabled:
            self._histogram.clear()
        self._sync_histogram_visible()

    def update_histogram(self, bins) -> None:
        """Push a fresh ISP histogram (no-op while hidden)."""
        if self._histogram.isVisible():
            self._histogram.set_histogram(bins)

    def _sync_histogram_visible(self) -> None:
        self._histogram.setVisible(self._hist_enabled and not self._frosted)
        self._histogram.raise_()

    def set_stats_overlay(self, enabled: bool) -> None:
        self._stats_enabled = bool(enabled)
        self._sync_stats_visible()

    def toggle_stats_overlay(self) -> None:
        self.set_stats_overlay(not self._stats_enabled)

    def update_stats(self, texts: dict[str, str | None]) -> None:
        """Push fresh field_texts. Hidden card only stores them, renders on show."""
        self._stats_texts = texts
        if self._stats_card.isVisible():
            self._render_stats()

    def _render_stats(self) -> None:
        if self._stats_texts is None:
            return
        self._stats_card.set_texts(self._stats_texts)
        self._place_stats_card()

    def _sync_stats_visible(self) -> None:
        visible = self._stats_enabled and not self._frosted
        if visible:
            self._render_stats()
        self._stats_card.setVisible(visible)
        self._stats_card.raise_()

    def _place_stats_card(self) -> None:
        self._stats_card.adjustSize()
        self._stats_card.move(self.width() - self._stats_card.width() - MARGIN, MARGIN)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Hidden card is re-placed on show.
        if self._stats_card.isVisible():
            self._place_stats_card()

    def set_assists(self, peaking: bool, zebra: bool, zebra_threshold: float) -> None:
        """Focus peaking / zebra overlays (no-op without a camera)."""
        if hasattr(self._live, "set_assists"):
            self._live.set_assists(peaking, zebra, zebra_threshold)
