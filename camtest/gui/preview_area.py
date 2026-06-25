"""PreviewArea - live camera preview with a frozen frost backdrop for modals.

The picamera2 GL preview is a native window that stacks above Qt widgets, so a
modal overlay cannot cover it. While a modal is up, a QStackedLayout swaps it for
a blurred still of the last frame (the stack keeps the slot's size, so nothing
collapses). The still is grabbed from an already-delivered frame, so the live
preview never hitches.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..qt import Qt, QtGui, QtWidgets, Signal

log = logging.getLogger(__name__)

# Frosted-glass strength: Gaussian blur radius as a fraction of the fitted frame
# width (resolution-independent). ~1/110 reads as a soft, smooth frost.
_BLUR_FRACTION = 1 / 110


class PreviewArea(QtWidgets.QWidget):
    """Stacks the live preview and a frozen, blurred still in one slot."""

    _snapshot_ready = Signal(object)  # PIL image, marshaled off the camera thread

    def __init__(self, engine, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self._engine = engine
        self._on_frozen: Callable[[], None] | None = None

        self._stack = QtWidgets.QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)

        if engine.picam2 is not None:
            self._live: QtWidgets.QWidget = engine.make_preview_widget()
        else:
            self._live = QtWidgets.QLabel("No camera detected")
            self._live.setAlignment(Qt.AlignCenter)
            self._live.setStyleSheet("font-size: 22px; color: #e06c75;")
        self._freeze = QtWidgets.QLabel()
        self._freeze.setAlignment(Qt.AlignCenter)
        # The frozen still is a full-resolution pixmap. Without this its size hint
        # inflates the stacked layout's minimum and pins the preview at full
        # height even after it is cleared, so the log can no longer claim space.
        self._freeze.setSizePolicy(QtWidgets.QSizePolicy.Ignored,
                                   QtWidgets.QSizePolicy.Ignored)
        self._stack.addWidget(self._live)
        self._stack.addWidget(self._freeze)

        # Grabbed on the camera thread, so a queued signal hops it to the GUI one.
        self._snapshot_ready.connect(self._on_snapshot)

    @property
    def has_camera(self) -> bool:
        return self._engine.picam2 is not None

    @property
    def is_frozen(self) -> bool:
        return self._stack.currentWidget() is self._freeze

    def lores_size(self) -> tuple[int, int]:
        """Current on-screen preview size, for sizing a new mode's lores stream."""
        return self.width(), self.height()

    def enter_freeze(self, on_frozen: Callable[[], None]) -> bool:
        """Grab and blur the last frame, swap it in, then call on_frozen().

        Keeps the live preview up until the still is ready (no flicker). Returns
        False if there is no camera, so the caller can proceed without a freeze.
        """
        if not self.has_camera:
            return False
        self._on_frozen = on_frozen
        if not self._engine.request_snapshot(
                lambda img: self._snapshot_ready.emit(img)):
            self._on_frozen = None
            return False
        return True

    def exit_freeze(self) -> None:
        """Restore the live preview and drop the frozen still."""
        self._stack.setCurrentWidget(self._live)
        self._freeze.clear()

    def _on_snapshot(self, img) -> None:
        cb, self._on_frozen = self._on_frozen, None
        if cb is None:
            return  # stale (modal already closed)
        pix = self._blur(img) if img is not None else None
        if pix is not None:
            self._freeze.setPixmap(pix)
        # Swap pages regardless, so the native GL preview is hidden behind the
        # modal even when the grab failed.
        self._stack.setCurrentWidget(self._freeze)
        cb()

    def _blur(self, img) -> "QtGui.QPixmap | None":
        # One-shot per modal open, so a PIL Gaussian blur is affordable. Fit to
        # the preview area first, then blur.
        from PIL import Image, ImageFilter
        try:
            img = img.convert("RGB")
            w, h = self.width(), self.height()
            if w > 0 and h > 0:
                scale = min(w / img.width, h / img.height)
                img = img.resize((max(1, round(img.width * scale)),
                                  max(1, round(img.height * scale))), Image.LANCZOS)
            img = img.filter(ImageFilter.GaussianBlur(
                max(4, round(img.width * _BLUR_FRACTION))))
            data = img.tobytes("raw", "RGB")
            qimg = QtGui.QImage(data, img.width, img.height,
                                3 * img.width, QtGui.QImage.Format_RGB888)
            return QtGui.QPixmap.fromImage(qimg)
        except Exception:
            log.exception("freeze-frame blur failed")
            return None
