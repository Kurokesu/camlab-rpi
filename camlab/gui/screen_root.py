# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""ScreenRoot places chrome pane and mirror view inside union window.

Cage hands app one window spanning every lit screen. In dual mode chrome
is put on one display and a display-only mirror on the other.
"""

from __future__ import annotations

from collections.abc import Callable

from ..qt import Qt, QtCore, QtWidgets


def chrome_screen(topology, chrome_on_dsi: bool = False) -> QtCore.QRect | None:
    """Chrome screen rect."""
    order = (topology.dsi, topology.monitor) if chrome_on_dsi else (topology.monitor, topology.dsi)
    for rect in order:
        if rect is not None:
            return rect
    return None if topology.bounds.isEmpty() else topology.bounds


def mirror_screen(topology, chrome_on_dsi: bool = False) -> QtCore.QRect | None:
    """Mirror screen rect."""
    if topology.dsi is None or topology.monitor is None:
        return None
    return topology.monitor if chrome_on_dsi else topology.dsi


def rect_text(rect: QtCore.QRect | None) -> str:
    """WxH+X+Y for logs, "none" for absent display."""
    if rect is None:
        return "none"
    return f"{rect.width()}x{rect.height()}+{rect.x()}+{rect.y()}"


class ScreenRoot(QtWidgets.QWidget):
    """Central widget placing children by screen rect."""

    def __init__(
        self,
        make_mirror_view: Callable[[QtWidgets.QWidget], QtWidgets.QWidget],
        forced: tuple[int, int] | None = None,
        parent: QtWidgets.QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("screenRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Selector scopes the black to the root, a bare rule cascades
        self.setStyleSheet("QWidget#screenRoot { background: #000; }")
        self.chrome_pane = QtWidgets.QWidget(self)
        self.mirror_view: QtWidgets.QWidget | None = None
        self._make_mirror_view = make_mirror_view
        self._forced = forced
        self._topology = None
        self._chrome_on_dsi = False

    def set_topology(self, topology) -> None:
        self._topology = topology
        self._place()

    @property
    def chrome_on_dsi(self) -> bool:
        """DSI carries chrome, as last claim left it."""
        return self._chrome_on_dsi

    def set_chrome_on_dsi(self, chrome_on_dsi: bool) -> None:
        """Swap panes so claimed display carries chrome."""
        if chrome_on_dsi == self._chrome_on_dsi:
            return
        self._chrome_on_dsi = chrome_on_dsi
        self._place()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place()

    def _rects(self) -> tuple[QtCore.QRect, QtCore.QRect | None]:
        """Chrome pane rect, then mirror view rect."""
        if self._forced is not None:
            # Panel preview: Cage forces fullscreen, shrink and center the pane instead
            pane = QtCore.QRect(QtCore.QPoint(), QtCore.QSize(*self._forced))
            pane.moveCenter(self.rect().center())
            return pane, None
        t = self._topology
        if t is None or (mirror := mirror_screen(t, self._chrome_on_dsi)) is None:
            return self.rect(), None
        origin = t.bounds.topLeft()
        chrome = chrome_screen(t, self._chrome_on_dsi)
        return chrome.translated(-origin), mirror.translated(-origin)

    def _place(self) -> None:
        pane, mirror = self._rects()
        self.chrome_pane.setGeometry(pane)
        if mirror is None:
            if self.mirror_view is not None:
                self.mirror_view.hide()
            return
        if self.mirror_view is None:
            self.mirror_view = self._make_mirror_view(self)
            # Covers stay above it, a hotplug blank spans both panes
            self.mirror_view.lower()
        self.mirror_view.setGeometry(mirror)
        self.mirror_view.show()
