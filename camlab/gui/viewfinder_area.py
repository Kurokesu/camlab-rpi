"""ViewfinderArea - hosts the live viewfinder widget.

Thin wrapper: owns the slot in the main layout and exposes the frost toggle
modals use. The viewfinder renders in-scene (see gl_viewfinder), so overlays
and sheets are plain Qt widgets stacked above it, no freeze-frame swap needed.
"""

from __future__ import annotations

import os

from ..qt import Qt, QtWidgets


class ViewfinderArea(QtWidgets.QWidget):
    def __init__(self, engine, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._engine = engine

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

    @property
    def has_camera(self) -> bool:
        return self._engine.picam2 is not None

    def lores_size(self) -> tuple[int, int]:
        """Current on-screen viewfinder size, for sizing a new mode's lores stream."""
        return self.width(), self.height()

    def set_frost(self, frosted: bool) -> None:
        """Blur the live viewfinder in-shader.

        Without a camera the placeholder text cannot blur, so it hides while
        frosted instead of shining sharply through the modal glass.
        """
        if hasattr(self._live, "set_frosted"):
            self._live.set_frosted(frosted)
        else:
            self._live.setVisible(not frosted)

    def set_assists(self, peaking: bool, zebra: bool,
                    zebra_threshold: float) -> None:
        """Focus peaking / zebra overlays (no-op without a camera)."""
        if hasattr(self._live, "set_assists"):
            self._live.set_assists(peaking, zebra, zebra_threshold)
