# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Black covers that hide layout churn.

BootCover blanks until first fullscreen (also hides blocking camera start).
SwitchCover blanks across hotplug. Boot treats oversized as settled so stuck
compositor cannot strand black. Switch does not: oversized is mid-relayout.
"""

from __future__ import annotations

from collections.abc import Callable

from ..qt import Qt, QtCore, QtWidgets, Signal

TargetRect = Callable[[], QtCore.QRect | None]


class _Cover(QtWidgets.QWidget):
    def __init__(self, host: QtWidgets.QWidget, window: QtWidgets.QWidget, target: TargetRect):
        super().__init__(host)
        self._host = host
        self._window = window
        # Window has settled once it spans this rect
        self._target = target
        # Plain QWidget subclasses ignore QSS backgrounds without this attribute.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background: #000;")

    def _target_width(self) -> int | None:
        rect = self._target()
        return None if rect is None else rect.width()

    def _single_shot(self, ms: int, slot) -> QtCore.QTimer:
        timer = QtCore.QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(ms)
        timer.timeout.connect(slot)
        return timer


class BootCover(_Cover):
    """Blank until first fullscreen, then lift for good."""

    revealed = Signal()

    _SETTLE_MS = 250
    _RETRY_MS = 3000
    _MAX_TRIES = 10

    def __init__(self, host: QtWidgets.QWidget, window: QtWidgets.QWidget, target: TargetRect):
        super().__init__(host, window, target)
        screen = QtWidgets.QApplication.primaryScreen()
        # Virtual geometry spans every lit screen, as the window will
        g = screen.virtualGeometry() if screen is not None else host.rect()
        self.setGeometry(0, 0, g.width(), g.height())
        self.raise_()
        # Show now so cover does not depend on construction order.
        self.show()

        self._settle = self._single_shot(self._SETTLE_MS, self._reveal)

        # Cold boot can hold the fullscreen configure past the settle window,
        # so retry until it lands and reveal regardless after _MAX_TRIES.
        self._tries = 0
        self._retry = self._single_shot(self._RETRY_MS, self._on_retry)
        self._retry.start()

    @property
    def settled(self) -> bool:
        width = self._target_width()
        return width is not None and self._window.width() >= width - 1

    def on_resize(self) -> bool:
        """Restart the settle countdown, reporting whether the window settled."""
        if not self.settled:
            self._settle.stop()
            return False
        self._settle.start()
        return True

    def sync_geometry(self, screen_rect: QtCore.QRect) -> None:
        """Cover the larger of screen and window, so no edge peeks out."""
        self.setGeometry(
            0,
            0,
            max(screen_rect.width(), self._window.width()),
            max(screen_rect.height(), self._window.height()),
        )

    def _on_retry(self) -> None:
        self._tries += 1
        if self.settled or self._tries >= self._MAX_TRIES:
            self._reveal()
        else:
            self._retry.start()

    def _reveal(self) -> None:
        self._settle.stop()
        self._retry.stop()
        self.hide()
        self.revealed.emit()


class SwitchCover(_Cover):
    """Blank across display hotplug. Lift once fullscreen settles."""

    _SETTLE_MS = 200
    _TIMEOUT_MS = 4000

    def __init__(self, host: QtWidgets.QWidget, window: QtWidgets.QWidget, target: TargetRect):
        super().__init__(host, window, target)
        self.hide()

        self._settle = self._single_shot(self._SETTLE_MS, self.lift)
        # Never leave the operator on black because a resize never arrived.
        self._timeout = self._single_shot(self._TIMEOUT_MS, self.lift)

    def blank(self) -> None:
        self._settle.stop()
        self.setGeometry(self._host.rect())
        self.raise_()
        self.show()
        self._timeout.start()

    def lift(self) -> None:
        self._settle.stop()
        self._timeout.stop()
        self.hide()

    def on_resize(self) -> None:
        if not self.isVisible():
            return
        self.setGeometry(self._host.rect())
        self.arm_lift()

    def arm_lift(self) -> None:
        """Count down to lifting once the window sits at screen width."""
        if not self.isVisible():
            return
        width = self._target_width()
        if width is not None and abs(self._window.width() - width) <= 1:
            self._settle.start()
        else:
            self._settle.stop()
