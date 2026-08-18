# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared in-window controls for kiosk UI.

Under Cage separate top-level surfaces (QDialog, QComboBox popups) misbehave.
ModalOverlay replaces dialogs. SegmentedSelector replaces dropdowns.
"""

from __future__ import annotations

from typing import Any

from ..qt import Qt, QtWidgets, Signal


def repolish(*widgets: QtWidgets.QWidget) -> None:
    """Re-evaluate QSS after a dynamic property change ([prop="..."] selectors)."""
    for w in widgets:
        w.style().unpolish(w)
        w.style().polish(w)


def hline(parent=None) -> QtWidgets.QFrame:
    """1px horizontal hairline (styled via QFrame#hsep)."""
    line = QtWidgets.QFrame(parent)
    line.setObjectName("hsep")
    line.setFixedHeight(1)
    return line


def vline(parent=None) -> QtWidgets.QFrame:
    """1px vertical hairline (styled via QFrame#vsep)."""
    line = QtWidgets.QFrame(parent)
    line.setObjectName("vsep")
    line.setFixedWidth(1)
    return line


def kinetic_scroll(viewport: QtWidgets.QWidget) -> None:
    """Drag-to-scroll on a scroll area viewport, vertical only.

    grabGesture sets WA_AcceptTouchEvents on the viewport, so the widget below
    stops seeing the mouse events Qt synthesizes from a finger drag.
    """
    QtWidgets.QScroller.grabGesture(viewport, QtWidgets.QScroller.ScrollerGestureType.TouchGesture)
    scroller = QtWidgets.QScroller.scroller(viewport)
    props = scroller.scrollerProperties()
    props.setScrollMetric(
        QtWidgets.QScrollerProperties.ScrollMetric.HorizontalOvershootPolicy,
        QtWidgets.QScrollerProperties.OvershootPolicy.OvershootAlwaysOff,
    )
    scroller.setScrollerProperties(props)


class SegmentedSelector(QtWidgets.QWidget):
    """Exclusive button row, dropdown replacement with no popup.

    changed fires only on user interaction. Programmatic rebuild is silent.
    """

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Fused segmented control: row reads as one pick-one widget. Arrow keys move within group.
        self._values: list[Any] = []
        self._group = QtWidgets.QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(lambda _id: self.changed.emit())
        self._row = QtWidgets.QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(0)

    def set_options(
        self,
        options: list[tuple[str, Any]],
        current: Any = None,
        enabled: bool = True,
        stretch: bool = True,
        disabled_values: tuple | list | set = (),
    ) -> None:
        """Populate (text, value) options, preselecting `current` if present.

        `stretch` trails expanding spacer to left-pack buttons in wide form.
        False keeps row hugging buttons. `disabled_values` grays out options,
        e.g. CSI port occupied by DSI display.
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
        for i, (text, value) in enumerate(options):
            btn = QtWidgets.QPushButton(text)
            btn.setObjectName("segment")
            # pos drives outer corner rounding. Non-first buttons overlap previous border by 1px.
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
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setEnabled(enabled and value not in disabled_values)
            self._group.addButton(btn, i)
            self._row.addWidget(btn)
        if stretch:
            self._row.addStretch(1)

        selectable = [i for i, v in enumerate(self._values) if self._group.button(i).isEnabled()]
        if current in self._values and self._values.index(current) in selectable:
            idx = self._values.index(current)
        else:
            idx = selectable[0] if selectable else 0
        if self._values:
            self._group.button(idx).setChecked(True)

    def button(self, value: Any) -> QtWidgets.QPushButton | None:
        """Segment button for `value`, to restyle in place without rebuilding the row."""
        if value not in self._values:
            return None
        return self._group.button(self._values.index(value))

    def set_value(self, value: Any) -> None:
        """Silently select `value` if present (no `changed` emission)."""
        if value in self._values:
            self._group.button(self._values.index(value)).setChecked(True)

    def current_value(self) -> Any:
        bid = self._group.checkedId()
        return self._values[bid] if 0 <= bid < len(self._values) else None

    def checked_button(self) -> QtWidgets.QAbstractButton | None:
        """Selected segment, the selector's single Tab stop."""
        return self._group.checkedButton()
