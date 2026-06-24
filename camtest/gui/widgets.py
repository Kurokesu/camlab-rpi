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


class IconChip(QtWidgets.QFrame):
    """A pill chip with a left icon image + text as two separate widgets.

    A single QLabel cannot truly vertically center an icon against text (rich
    text aligns a font glyph to the baseline and an <img> only to keyword
    positions, both visibly off). Laying the icon (a pixmap) and the text out as
    two widgets in an HBox lets Qt center each one, exactly like QPushButton does
    for its icon + text. Style the pill via the `class`/objectName.
    """

    def __init__(self, parent=None, *, object_name: str = "",
                 chip_class: str = "chip", icon_size: int = 21):
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        if chip_class:
            self.setProperty("class", chip_class)
        self._icon_size = icon_size
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(9, 3, 9, 3)
        row.setSpacing(6)
        self.icon_lbl = QtWidgets.QLabel(self)
        self.icon_lbl.setObjectName("chipIcon")
        self.icon_lbl.setAlignment(Qt.AlignCenter)
        self.text_lbl = QtWidgets.QLabel(self)
        self.text_lbl.setObjectName("chipText")
        row.addWidget(self.icon_lbl)
        row.addWidget(self.text_lbl)

    def icon_size(self) -> int:
        return self._icon_size

    def set_content(self, pixmap: Any, text: str) -> None:
        """Set the icon pixmap (None/null hides it) and the (rich) text."""
        if pixmap is not None and not pixmap.isNull():
            self.icon_lbl.setPixmap(pixmap)
            self.icon_lbl.show()
        else:
            self.icon_lbl.clear()
            self.icon_lbl.hide()
        self.text_lbl.setText(text)

    def set_state(self, state: str) -> None:
        """Set a `state` property and re-polish so the stylesheet re-applies."""
        self.setProperty("state", state)
        for w in (self, self.text_lbl, self.icon_lbl):
            w.style().unpolish(w)
            w.style().polish(w)


class SegmentedSelector(QtWidgets.QWidget):
    """An exclusive row of buttons - a dropdown replacement with no popup.

    `changed` fires only on user interaction. Rebuilding the options or setting
    the value programmatically is silent, so dependent cascades do not recurse.
    """

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values: list[Any] = []
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(lambda _id: self.changed.emit())
        self._row = QtWidgets.QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(6)

    def set_options(self, options: list[tuple[str, Any]], current: Any = None,
                    enabled: bool = True) -> None:
        """Populate (text, value) options, preselecting `current` if present."""
        for btn in self._group.buttons():
            self._group.removeButton(btn)
            btn.deleteLater()
        while self._row.count():
            item = self._row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        self._values = [value for _text, value in options]
        for i, (text, _value) in enumerate(options):
            btn = QtWidgets.QPushButton(text)
            btn.setObjectName("segment")
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setEnabled(enabled)
            self._group.addButton(btn, i)
            self._row.addWidget(btn)
        self._row.addStretch(1)

        idx = self._values.index(current) if current in self._values else 0
        if self._values:
            self._group.button(idx).setChecked(True)

    def current_value(self) -> Any:
        bid = self._group.checkedId()
        return self._values[bid] if 0 <= bid < len(self._values) else None
