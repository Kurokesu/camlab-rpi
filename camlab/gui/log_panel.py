"""Collapsible log panel - shows the captured camera-stack stderr stream.

Lines matching an integrity pattern are coloured by severity (errors red,
warnings orange) and can be isolated with a mutually-exclusive All/Warnings/
Errors filter, so warnings are not buried when errors flood the stream. The
panel keeps a bounded ring buffer so the filter re-renders without re-tailing.
"""

from __future__ import annotations

import collections
import html

from ..integrity import LogClassifier
from ..qt import Qt, QtGui, QtWidgets
from .widgets import SegmentedSelector

_MAX_LINES = 2000

# Severity -> log line colour (matches the status-strip counters).
_SEV_COLOR = {"error": "#e06c75", "warning": "#e5c07b"}


class LogPanel(QtWidgets.QWidget):
    def __init__(self, classifier: LogClassifier | None = None, parent=None):
        super().__init__(parent)
        self._classifier = classifier or LogClassifier()
        # (line, severity): severity is None for unmatched lines.
        self._buffer: collections.deque[tuple[str, str | None]] = \
            collections.deque(maxlen=_MAX_LINES)
        self._filter = "all"

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(12, 4, 12, 4)
        header.setSpacing(22)
        title = QtWidgets.QLabel("Log")
        title.setObjectName("logTitle")

        self.filter = SegmentedSelector()
        self.filter.set_options(
            [("All", "all"), ("Warnings", "warning"), ("Errors", "error")],
            current="all", stretch=False)
        self.filter.changed.connect(self._on_filter)

        self.autoscroll = QtWidgets.QCheckBox("autoscroll")
        self.autoscroll.setToolTip("Follow new lines. Uncheck to freeze the view for "
                                   "inspection. New lines keep buffering and reappear "
                                   "when re-checked.")
        self.autoscroll.setChecked(True)
        # Indicator to the right of the label (reads label-then-box).
        self.autoscroll.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.autoscroll.toggled.connect(self._on_autoscroll)

        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self.clear)

        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.filter)
        header.addWidget(self.autoscroll)
        header.addWidget(clear_btn)

        self.view = QtWidgets.QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setObjectName("logView")
        self.view.setMaximumBlockCount(_MAX_LINES)
        font = QtGui.QFont("monospace")
        font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        font.setPointSize(9)
        self.view.setFont(font)

        lay.addLayout(header)
        lay.addWidget(self.view)

    def append_line(self, line: str) -> None:
        _cat, sev = self._classifier.classify_with_severity(line)
        self._buffer.append((line, sev))
        # Paused (autoscroll off): keep recording, but leave the view frozen so the
        # operator can read it without it moving. It catches up when re-checked.
        if not self.autoscroll.isChecked():
            return
        if not self._passes(sev):
            return
        self._append_html(line, sev)
        self._scroll_to_bottom()

    def _passes(self, sev: str | None) -> bool:
        return self._filter == "all" or sev == self._filter

    def _append_html(self, line: str, sev: str | None) -> None:
        safe = html.escape(line)
        color = _SEV_COLOR.get(sev or "")
        if color:
            safe = f"<span style='color:{color}'>{safe}</span>"
        self.view.appendHtml(safe)

    def _scroll_to_bottom(self) -> None:
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_filter(self) -> None:
        self._filter = self.filter.current_value() or "all"
        self._rerender()

    def _on_autoscroll(self, checked: bool) -> None:
        # Re-checking catches the view up with everything buffered while paused.
        # Unchecking does nothing here, which is what freezes the current view.
        if checked:
            self._rerender()

    def _rerender(self) -> None:
        self.view.clear()
        for line, sev in self._buffer:
            if self._passes(sev):
                self._append_html(line, sev)
        if self.autoscroll.isChecked():
            self._scroll_to_bottom()

    def clear(self) -> None:
        self._buffer.clear()
        self.view.clear()
