"""In-window modal overlays.

The kiosk runs as a single fullscreen surface under Cage. Separate top-level
windows (QDialog / QMessageBox) render unreliably there - they collapse to a tiny
artifact - and a child QDialog over the EGL surface either eats input or paints
an opaque background over the chrome. So modal UI is a plain child QWidget drawn
over the main surface, which renders and routes mouse input correctly.

A QWidget is not a focus scope, so the part QDialog would give for free - keeping
Tab inside the card - is added here via an app-level event filter. Enter and
Escape are handled by MainWindow's window shortcuts (they fire regardless of which
child holds focus), so one path covers both the main screen and the overlay.

Note: the caller must hide any native child (the QGlPicamera2 GL preview) while an
overlay is shown, because native windows stack above Qt-painted widgets and would
otherwise cover it. MainWindow does this in _open_modal.
"""

from __future__ import annotations

from collections.abc import Callable

from ..qt import QtCore, QtGui, QtWidgets

_DIM = QtGui.QColor(12, 13, 16, 165)


class ModalOverlay(QtWidgets.QWidget):
    """Covers its host, dims it, blocks input, and centers a card widget.

    The dim skips an optional clear_rect (the frozen-preview area) so a frosted
    backdrop reads at full strength while the surrounding chrome stays dimmed.

    Behaves as a modal: an app-level event filter keeps Tab inside the card (one
    stop per SegmentedSelector, the rest individual), and backdrop clicks are
    swallowed so the dimmed chrome stays inert. Enter/Escape come from MainWindow.
    """

    def __init__(self, host: QtWidgets.QWidget, card: QtWidgets.QWidget,
                 clear_rect: QtCore.QRect | None = None):
        super().__init__(host)
        self._host = host
        self.card = card
        self._clear_rect = clear_rect
        self.setObjectName("modalOverlay")
        # Every clickable in the card gets the pointing-hand cursor (the action
        # buttons would otherwise keep the default arrow).
        for btn in card.findChildren(QtWidgets.QPushButton):
            btn.setCursor(QtCore.Qt.PointingHandCursor)
        # Hold focus so the dimmed chrome behind cannot be tabbed to until the
        # first Tab moves into the card (see _tab_targets / eventFilter).
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(40, 40, 40, 40)
        outer.addStretch(1)
        row = QtWidgets.QHBoxLayout()
        row.addStretch(1)
        row.addWidget(card)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(1)

        host.installEventFilter(self)
        # Trap Tab and Enter for the whole app while shown: a plain QWidget is not
        # a focus scope, so without this Tab would escape into the dimmed chrome.
        QtWidgets.QApplication.instance().installEventFilter(self)
        self.setGeometry(host.rect())
        self.raise_()
        self.show()
        # Focus the overlay itself (not a button), so nothing is highlighted until
        # the first Tab, which then lands on the first control inside the card.
        self.setFocus(QtCore.Qt.OtherFocusReason)

    def paintEvent(self, event) -> None:
        painter = QtGui.QPainter(self)
        if self._clear_rect is not None and self._clear_rect.isValid():
            # Dim everything but the frozen-preview rect.
            region = QtGui.QRegion(self.rect()).subtracted(
                QtGui.QRegion(self._clear_rect))
            painter.setClipRegion(region)
        painter.fillRect(self.rect(), _DIM)

    def mousePressEvent(self, event) -> None:
        # Swallow backdrop clicks so the dimmed chrome underneath stays inert.
        event.accept()

    def _tab_targets(self) -> list[QtWidgets.QWidget]:
        """Card widgets that are Tab stops, in order.

        Each SegmentedSelector contributes one stop (its checked segment); arrow
        keys move within it, the native radio convention. Action buttons are
        individual stops.
        """
        targets: list[QtWidgets.QWidget] = []
        seen_selectors: set[int] = set()
        for w in self.card.findChildren(QtWidgets.QWidget):
            if not (w.isEnabled() and w.isVisibleTo(self.card)
                    and w.focusPolicy() & QtCore.Qt.TabFocus):
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
        from .widgets import SegmentedSelector
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
        targets[idx].setFocus(QtCore.Qt.TabFocusReason)

    def eventFilter(self, obj, event) -> bool:
        if obj is self._host and event.type() == QtCore.QEvent.Resize:
            self.setGeometry(self._host.rect())
            return False
        # App-wide key trap while the overlay is up.
        if event.type() == QtCore.QEvent.KeyPress:
            key = event.key()
            if key in (QtCore.Qt.Key_Tab, QtCore.Qt.Key_Backtab):
                back = key == QtCore.Qt.Key_Backtab or bool(
                    event.modifiers() & QtCore.Qt.ShiftModifier)
                self._cycle_focus(forward=not back)
                return True  # consume: focus stays inside the card
        return super().eventFilter(obj, event)

    def dismiss(self) -> None:
        self._host.removeEventFilter(self)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.hide()
        self.deleteLater()


Button = tuple[str, str, Callable[[], None]]  # (label, role, callback) - role: "" | "danger"


def message_card(title: str, message: str, buttons: list[Button]) -> QtWidgets.QFrame:
    """A simple confirmation / information card for use inside a ModalOverlay."""
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
        # Last non-danger button (typically OK) is the Enter target. Fall back to
        # the last button if every action is destructive.
        if role != "danger" or primary is None:
            primary = btn
    lay.addLayout(row)
    if primary is not None:
        card.primary_button = primary
    return card
