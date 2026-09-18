# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Collapsible log panel for captured stderr.

Ring buffer lets filter re-render without re-tailing. Boot-to-viewfinder time in header.
"""

from __future__ import annotations

import collections
import html

from ..integrity import PANEL_LINES, IntegrityStats, LogClassifier
from ..qt import Qt, QtCore, QtGui, QtWidgets, Signal, Slot
from .style import SEV_COLOR
from .widgets import SegmentedSelector, kinetic_scroll, repolish

_TRIM_SLACK = 256

_POINTER_EVENTS = frozenset(
    {
        QtCore.QEvent.Type.MouseButtonPress,
        QtCore.QEvent.Type.MouseButtonRelease,
        QtCore.QEvent.Type.MouseButtonDblClick,
        QtCore.QEvent.Type.MouseMove,
    }
)


class LogPanel(QtWidgets.QWidget):
    # Clearing the view also resets the counts, so the two cannot disagree.
    cleared = Signal()

    def __init__(self, classifier: LogClassifier | None = None, parent=None):
        super().__init__(parent)
        self._classifier = classifier or LogClassifier()
        self._buffer: collections.deque[tuple[str, str | None]] = collections.deque(
            maxlen=PANEL_LINES
        )
        self._filter = "all"
        self._pending = False  # lines arrived while frozen
        self._syncing = False  # our own scrolling, not the operator's

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(12, 4, 12, 4)
        header.setSpacing(22)
        title = QtWidgets.QLabel("Log")
        title.setObjectName("logTitle")

        self.boot_lbl = QtWidgets.QLabel(self)
        self.boot_lbl.setObjectName("bootInfo")

        self.filter = SegmentedSelector()
        self.filter.set_options(
            [("All", "all"), ("Warnings", "warning"), ("Errors", "error")],
            current="all",
            stretch=False,
        )
        self.filter.changed.connect(self._on_filter)

        self.autoscroll_btn = QtWidgets.QPushButton("Autoscroll")
        self.autoscroll_btn.setCheckable(True)
        self.autoscroll_btn.setChecked(True)
        self.autoscroll_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.autoscroll_btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.autoscroll_btn.toggled.connect(self._on_autoscroll)

        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        clear_btn.clicked.connect(self.clear)

        header.addWidget(title)
        header.addWidget(self.boot_lbl)
        header.addStretch(1)
        header.addWidget(self.filter)
        header.addWidget(self.autoscroll_btn)
        header.addWidget(clear_btn)

        self.view = QtWidgets.QTextEdit()
        self.view.setReadOnly(True)
        # Read-only still records undo history, which grows without bound here.
        self.view.setUndoRedoEnabled(False)
        self.view.setObjectName("logView")
        font = QtGui.QFont("monospace")
        font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        font.setPointSize(9)
        self.view.setFont(font)
        viewport = self.view.viewport()
        kinetic_scroll(viewport)
        viewport.installEventFilter(self)
        self.view.verticalScrollBar().valueChanged.connect(self._on_scrolled)

        lay.addLayout(header)
        lay.addWidget(self.view)

        self.set_boot_time(None)
        self.update_integrity(IntegrityStats())

    def eventFilter(self, obj, ev) -> bool:
        """Drop mouse events Qt synthesizes from finger drags, the text edit reads them
        as a selection drag. QScroller scrolls off the touch stream, real mouse still selects.
        """
        if obj is self.view.viewport() and ev.type() in _POINTER_EVENTS:
            dev = ev.pointingDevice()
            if dev is not None and dev.type() == QtGui.QInputDevice.DeviceType.TouchScreen:
                return True
        return super().eventFilter(obj, ev)

    # session diagnostics
    def set_boot_time(self, seconds: float | None) -> None:
        value = f"{seconds:.1f}s" if seconds is not None else "..."
        self.boot_lbl.setText(f"boot time {value}")

    @Slot(object)
    def update_integrity(self, stats: IntegrityStats) -> None:
        """Counts ride filter segments they select for, tinted when non-zero."""
        for value, word in (("warning", "Warnings"), ("error", "Errors")):
            btn = self.filter.button(value)
            if btn is None:
                continue
            count = stats.total(value)
            text = f"{word} {count}"
            if btn.text() != text:
                btn.setText(text)
                # Ratchet width so a digit rollover cannot shift the header.
                btn.setMinimumWidth(max(btn.minimumWidth(), btn.sizeHint().width()))
            sev = value if count else None
            # Tint flips are rare, restyle only then.
            if btn.property("sev") != sev:
                btn.setProperty("sev", sev)
                repolish(btn)

    # log stream
    def append_line(self, line: str) -> None:
        _cat, sev = self._classifier.classify_with_severity(line)
        self._buffer.append((line, sev))
        # Frozen: keep recording but leave the view still. It catches up on resume.
        if not self.autoscroll_btn.isChecked():
            self._pending = True
            return
        if not self._passes(sev):
            return
        cur = QtGui.QTextCursor(self.view.document())
        cur.movePosition(QtGui.QTextCursor.MoveOperation.End)
        self._insert_line(cur, line, sev)
        self._trim()
        self._scroll_to_bottom()

    def _passes(self, sev: str | None) -> bool:
        return self._filter == "all" or sev == self._filter

    @staticmethod
    def _insert_line(cur: QtGui.QTextCursor, line: str, sev: str | None) -> None:
        safe = html.escape(line)
        color = SEV_COLOR.get(sev or "")
        if color:
            safe = f"<span style='color:{color}'>{safe}</span>"
        # Fresh formats, else severity color bleeds into following lines.
        if not cur.document().isEmpty():
            cur.insertBlock(QtGui.QTextBlockFormat(), QtGui.QTextCharFormat())
        cur.insertHtml(safe)

    def _trim(self) -> None:
        """Drop oldest lines in batches, cheaper than trimming on every append."""
        doc = self.view.document()
        if doc.blockCount() <= PANEL_LINES + _TRIM_SLACK:
            return
        self._syncing = True
        cur = QtGui.QTextCursor(doc.firstBlock())
        cur.movePosition(
            QtGui.QTextCursor.MoveOperation.NextBlock,
            QtGui.QTextCursor.MoveMode.KeepAnchor,
            doc.blockCount() - PANEL_LINES,
        )
        cur.removeSelectedText()
        self._syncing = False

    def _scroll_to_bottom(self) -> None:
        sb = self.view.verticalScrollBar()
        self._syncing = True
        sb.setValue(sb.maximum())
        self._syncing = False

    def _on_filter(self) -> None:
        self._filter = self.filter.current_value() or "all"
        self._rerender()

    def _on_scrolled(self, value: int) -> None:
        """Leaving the bottom freezes the view, returning resumes."""
        if self._syncing:
            return
        # Scrollbar counts pixels, so grant a line of grace before calling it away.
        slop = self.view.fontMetrics().lineSpacing()
        at_bottom = value >= self.view.verticalScrollBar().maximum() - slop
        if at_bottom != self.autoscroll_btn.isChecked():
            self.autoscroll_btn.setChecked(at_bottom)

    def _on_autoscroll(self, checked: bool) -> None:
        # Only pay for a re-render when lines actually arrived while frozen.
        if not checked:
            return
        if self._pending:
            self._rerender()
        else:
            self._scroll_to_bottom()

    def _rerender(self) -> None:
        self._syncing = True
        self.view.clear()
        cur = QtGui.QTextCursor(self.view.document())
        cur.movePosition(QtGui.QTextCursor.MoveOperation.End)
        # One edit block, so 2000 inserts relayout once instead of 2000 times.
        cur.beginEditBlock()
        try:
            for line, sev in self._buffer:
                if self._passes(sev):
                    self._insert_line(cur, line, sev)
        finally:
            cur.endEditBlock()
        self._syncing = False
        self._pending = False
        if self.autoscroll_btn.isChecked():
            self._scroll_to_bottom()

    def clear(self) -> None:
        self._buffer.clear()
        self._pending = False
        self._syncing = True
        self.view.clear()
        self._syncing = False
        self.cleared.emit()
