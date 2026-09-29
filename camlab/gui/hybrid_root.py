# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""HybridRoot places panel pane and mirror view inside the union window.

Cage hands the app one window spanning every lit screen. Panel pane sits on the
panel rect, display-only mirror view on the monitor rect. With one head lit the
panel pane fills the window.
"""

from __future__ import annotations

from collections.abc import Callable

from ..qt import Qt, QtCore, QtWidgets


def pane_screen(topology) -> QtCore.QRect | None:
    """Screen rect the panel pane lands on: panel, else whatever is lit."""
    if topology.panel is not None:
        return topology.panel
    return None if topology.bounds.isEmpty() else topology.bounds


def rect_text(rect: QtCore.QRect | None) -> str:
    """WxH+X+Y for logs, "none" for an absent head."""
    if rect is None:
        return "none"
    return f"{rect.width()}x{rect.height()}+{rect.x()}+{rect.y()}"


class HybridRoot(QtWidgets.QWidget):
    """Central widget placing children by screen rect."""

    def __init__(
        self,
        make_mirror_view: Callable[[QtWidgets.QWidget], QtWidgets.QWidget],
        forced: tuple[int, int] | None = None,
        parent: QtWidgets.QWidget | None = None,
    ):
        super().__init__(parent)
        self.setObjectName("hybridRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # Selector scopes the black to the root, a bare rule cascades
        self.setStyleSheet("QWidget#hybridRoot { background: #000; }")
        self.panel_pane = QtWidgets.QWidget(self)
        self.mirror_view: QtWidgets.QWidget | None = None
        self._make_mirror_view = make_mirror_view
        self._forced = forced
        self._topology = None

    def set_topology(self, topology) -> None:
        self._topology = topology
        self._place()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place()

    def _rects(self) -> tuple[QtCore.QRect, QtCore.QRect | None]:
        if self._forced is not None:
            # Panel preview: Cage forces fullscreen, shrink and center the pane instead
            panel = QtCore.QRect(QtCore.QPoint(), QtCore.QSize(*self._forced))
            panel.moveCenter(self.rect().center())
            return panel, None
        t = self._topology
        if t is None or t.panel is None or t.monitor is None:
            return self.rect(), None
        origin = t.bounds.topLeft()
        return t.panel.translated(-origin), t.monitor.translated(-origin)

    def _place(self) -> None:
        panel, monitor = self._rects()
        self.panel_pane.setGeometry(panel)
        if monitor is None:
            if self.mirror_view is not None:
                self.mirror_view.hide()
            return
        if self.mirror_view is None:
            self.mirror_view = self._make_mirror_view(self)
            # Covers stay above it, a hotplug blank spans both panes
            self.mirror_view.lower()
        self.mirror_view.setGeometry(monitor)
        self.mirror_view.show()
