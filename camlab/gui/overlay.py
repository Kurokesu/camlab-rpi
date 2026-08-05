# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""In-window modal overlays.

Cage collapses top-level dialogs to a tiny artifact. A child QDialog over EGL
either eats input or paints opaque over chrome. Modal UI is a plain child
QWidget on the main surface. Tab stays in the card via an app-level filter.
Enter/Escape come from window shortcuts.
"""

from __future__ import annotations

from collections.abc import Callable

from ..qt import QtCore, QtGui, QtWidgets
from .widgets import SegmentedSelector

_DIM = QtGui.QColor(12, 13, 16, 165)


class ModalOverlay(QtWidgets.QWidget):
    """Covers host, dims it, blocks input and centres a card.

    Dim skips optional clear_rect (frosted viewfinder) so frost stays full
    strength. Tab stays in card.
    """

    def __init__(
        self,
        host: QtWidgets.QWidget,
        card: QtWidgets.QWidget,
        clear_rect: QtCore.QRect | None = None,
        margin: int = 40,
        on_backdrop: Callable[[], None] | None = None,
    ):
        super().__init__(host)
        self._host = host
        self.card = card
        self._clear_rect = clear_rect
        self._on_backdrop = on_backdrop
        self.setObjectName("modalOverlay")
        for btn in card.findChildren(QtWidgets.QPushButton):
            btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        # Hold focus so dimmed chrome cannot be tabbed before card.
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(margin, margin, margin, margin)
        outer.addStretch(1)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        host.installEventFilter(self)
        # Plain QWidget is not a focus scope: without an app trap, Tab escapes.
        self._app = QtWidgets.QApplication.instance()
        if self._app is not None:
            self._app.installEventFilter(self)
        self.setGeometry(host.rect())
        self.raise_()
        self.show()
        # Focus overlay, not a button, until first Tab.
        self.setFocus(QtCore.Qt.FocusReason.OtherFocusReason)

    def paintEvent(self, event) -> None:
        painter = QtGui.QPainter(self)
        if self._clear_rect is not None and self._clear_rect.isValid():
            region = QtGui.QRegion(self.rect()).subtracted(QtGui.QRegion(self._clear_rect))
            painter.setClipRegion(region)
        painter.fillRect(self.rect(), _DIM)

    def mousePressEvent(self, event) -> None:
        # Card widgets ignore presses as well, so geometry decides what is backdrop.
        if self._on_backdrop is not None and not self.card.geometry().contains(
            event.position().toPoint()
        ):
            self._on_backdrop()
        event.accept()

    def _tab_targets(self) -> list[QtWidgets.QWidget]:
        """Card Tab stops in order. Each SegmentedSelector is one stop
        (checked segment). Arrows move within it."""
        targets: list[QtWidgets.QWidget] = []
        seen_selectors: set[int] = set()
        for w in self.card.findChildren(QtWidgets.QWidget):
            if not (
                w.isEnabled()
                and w.isVisibleTo(self.card)
                and w.focusPolicy().value & QtCore.Qt.FocusPolicy.TabFocus.value
            ):
                continue
            sel = self._selector_of(w)
            if sel is not None:
                if id(sel) in seen_selectors:
                    continue
                seen_selectors.add(id(sel))
                stop = sel.checked_button() or w
                targets.append(stop)
            else:
                targets.append(w)
        return targets

    @staticmethod
    def _selector_of(w: QtWidgets.QWidget):
        p = w.parent()
        while p is not None:
            if isinstance(p, SegmentedSelector):
                return p
            p = p.parent()
        return None

    def _cycle_focus(self, forward: bool) -> None:
        targets = self._tab_targets()
        if not targets:
            return
        cur = QtWidgets.QApplication.focusWidget()
        if cur in targets:
            idx = (targets.index(cur) + (1 if forward else -1)) % len(targets)
        else:
            idx = 0 if forward else len(targets) - 1
        targets[idx].setFocus(QtCore.Qt.FocusReason.TabFocusReason)

    def eventFilter(self, obj, event) -> bool:
        if obj is self._host and event.type() == QtCore.QEvent.Type.Resize:
            self.setGeometry(self._host.rect())
            return False
        # App-wide key trap while overlay is up.
        if event.type() == QtCore.QEvent.Type.KeyPress:
            key = event.key()
            if key in (QtCore.Qt.Key.Key_Tab, QtCore.Qt.Key.Key_Backtab):
                back = key == QtCore.Qt.Key.Key_Backtab or bool(
                    event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier
                )
                self._cycle_focus(forward=not back)
                return True  # consume: focus stays in card
        return super().eventFilter(obj, event)

    def dismiss(self) -> None:
        self._host.removeEventFilter(self)
        if self._app is not None:
            self._app.removeEventFilter(self)
        self.hide()
        self.deleteLater()


Button = tuple[str, str, Callable[[], None]]  # (label, role, callback) - role: "" | "danger"


def message_card(title: str, message: str, buttons: list[Button]) -> QtWidgets.QFrame:
    """Simple confirmation / info card for ModalOverlay."""
    card = QtWidgets.QFrame()
    card.setObjectName("modalCard")
    card.setMinimumWidth(380)

    lay = QtWidgets.QVBoxLayout(card)
    lay.setContentsMargins(22, 20, 22, 18)
    lay.setSpacing(14)

    title_lbl = QtWidgets.QLabel(title)
    title_lbl.setObjectName("modalTitle")
    lay.addWidget(title_lbl)

    if message:
        msg_lbl = QtWidgets.QLabel(message)
        msg_lbl.setObjectName("modalText")
        msg_lbl.setWordWrap(True)
        lay.addWidget(msg_lbl)

    row = QtWidgets.QHBoxLayout()
    row.addStretch(1)
    primary = None
    for label, role, callback in buttons:
        btn = QtWidgets.QPushButton(label)
        if role == "danger":
            btn.setObjectName("danger")
        btn.clicked.connect(callback)
        row.addWidget(btn)
        # Last non-danger button is Enter target. Fall back if all destructive.
        if role != "danger" or primary is None:
            primary = btn
    lay.addLayout(row)
    if primary is not None:
        card.primary_button = primary
    return card
