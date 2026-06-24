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
