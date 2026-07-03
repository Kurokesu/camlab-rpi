"""Shared in-window controls for the kiosk UI.

Under the Cage compositor the app renders as a single fullscreen surface, and
separate top-level surfaces (QDialog/QMessageBox, and also QComboBox dropdown
popups) misbehave - they render as a tiny artifact or open at the screen corner.
ModalOverlay already replaces dialogs. SegmentedSelector replaces dropdowns with
an inline exclusive button row, so every choice stays on the main surface.
"""

from __future__ import annotations

from typing import Any

from ..qt import Qt, QtWidgets, Signal


def hline(parent=None) -> QtWidgets.QFrame:
    """A 1px horizontal hairline (styled via QFrame#hsep in the app stylesheet)."""
    line = QtWidgets.QFrame(parent)
    line.setObjectName("hsep")
    line.setFixedHeight(1)
    return line


def vline(parent=None) -> QtWidgets.QFrame:
    """A 1px vertical hairline (styled via QFrame#vsep in the app stylesheet)."""
    line = QtWidgets.QFrame(parent)
    line.setObjectName("vsep")
    line.setFixedWidth(1)
    return line


class SegmentedSelector(QtWidgets.QWidget):
    """An exclusive row of buttons - a dropdown replacement with no popup.

    `changed` fires only on user interaction. Rebuilding the options or setting
    the value programmatically is silent, so dependent cascades do not recurse.
    """

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # A fused segmented control: a row of buttons whose shared borders collapse
        # into single hairlines (via a -1px left margin) and which round only their
        # outer corners, so the row reads as one control - "pick one". Each button
        # rounds itself (no parent-pill clipping artifacts). Arrow keys move within
        # the exclusive group (the radio convention).
        self._values: list[Any] = []
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(lambda _id: self.changed.emit())
        self._row = QtWidgets.QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(0)

    def set_options(self, options: list[tuple[str, Any]], current: Any = None,
                    enabled: bool = True, stretch: bool = True) -> None:
        """Populate (text, value) options, preselecting `current` if present.

        `stretch` trails the row with an expanding spacer (left-packs the
        buttons in a wide form); pass False to keep the row hugging its buttons
        (e.g. inline in a toolbar header).
        """
        for btn in self._group.buttons():
            self._group.removeButton(btn)
            btn.deleteLater()
        while self._row.count():
            item = self._row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        self._values = [value for _text, value in options]
        last = len(options) - 1
        for i, (text, _value) in enumerate(options):
            btn = QtWidgets.QPushButton(text)
            btn.setObjectName("segment")
            # Position drives which outer corners round; non-first buttons overlap
            # the previous border by 1px so the shared edge is a single hairline.
            if last == 0:
                pos = "only"
            elif i == 0:
                pos = "first"
            elif i == last:
                pos = "last"
            else:
                pos = "mid"
            btn.setProperty("pos", pos)
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setEnabled(enabled)
            self._group.addButton(btn, i)
            self._row.addWidget(btn)
        if stretch:
            self._row.addStretch(1)

        idx = self._values.index(current) if current in self._values else 0
        if self._values:
            self._group.button(idx).setChecked(True)

    def set_value(self, value: Any) -> None:
        """Silently select `value` if present (no `changed` emission)."""
        if value in self._values:
            self._group.button(self._values.index(value)).setChecked(True)

    def current_value(self) -> Any:
        bid = self._group.checkedId()
        return self._values[bid] if 0 <= bid < len(self._values) else None

    def checked_button(self) -> "QtWidgets.QAbstractButton | None":
        """The selected segment, the selector's single Tab stop."""
        return self._group.checkedButton()
