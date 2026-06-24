"""In-window modal overlays.

The kiosk runs as a single fullscreen surface under Cage. Separate top-level
windows (QDialog / QMessageBox) render unreliably there - they collapse to a
tiny unusable artifact - so all modal UI is drawn as a child widget covering the
main window instead.

Note: the caller must hide any native child (the QGlPicamera2 GL preview) while
an overlay is shown, because native windows stack above Qt-painted widgets and
would otherwise cover the overlay. MainWindow does this in _open_modal.
"""

from __future__ import annotations

from collections.abc import Callable

from ..qt import Qt, QtCore, QtWidgets


class ModalOverlay(QtWidgets.QWidget):
    """Covers its host, dims it, blocks input, and centers a card widget."""

    def __init__(self, host: QtWidgets.QWidget, card: QtWidgets.QWidget):
        super().__init__(host)
        self._host = host
        self.setObjectName("modalOverlay")
        self.setAttribute(Qt.WA_StyledBackground, True)

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
        self.setGeometry(host.rect())
        self.raise_()
        self.show()

    def eventFilter(self, obj, event) -> bool:
        if obj is self._host and event.type() == QtCore.QEvent.Resize:
            self.setGeometry(self._host.rect())
        return super().eventFilter(obj, event)

    def dismiss(self) -> None:
        self._host.removeEventFilter(self)
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
    for label, role, callback in buttons:
        btn = QtWidgets.QPushButton(label)
        if role == "danger":
            btn.setObjectName("danger")
        btn.clicked.connect(callback)
        row.addWidget(btn)
    lay.addLayout(row)
    return card
