"""Collapsible log panel - shows the captured camera-stack stderr stream.

Lines matching an integrity pattern are highlighted. An "errors only" filter and
a clear button help the operator focus. The panel keeps a bounded ring buffer so
the filter can re-render without re-tailing.
"""

from __future__ import annotations

import collections
import html

from ..integrity import LogClassifier
from ..qt import Qt, QtGui, QtWidgets

_MAX_LINES = 2000


class LogPanel(QtWidgets.QWidget):
    def __init__(self, classifier: LogClassifier | None = None, parent=None):
        super().__init__(parent)
        self._classifier = classifier or LogClassifier()
        self._buffer: collections.deque[tuple[str, str | None]] = collections.deque(maxlen=_MAX_LINES)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = QtWidgets.QHBoxLayout()
        header.setContentsMargins(12, 4, 12, 4)
        header.setSpacing(26)
        title = QtWidgets.QLabel("Log")
        title.setObjectName("logTitle")
        self.errors_only = QtWidgets.QCheckBox("errors only")
        self.errors_only.setToolTip("Show only lines that match an integrity pattern.")
        self.errors_only.toggled.connect(self._rerender)
        self.autoscroll = QtWidgets.QCheckBox("autoscroll")
        self.autoscroll.setToolTip("Follow new lines. Uncheck to freeze the view for "
                                   "inspection. New lines keep buffering and reappear "
                                   "when re-checked.")
        self.autoscroll.setChecked(True)
        self.autoscroll.toggled.connect(self._on_autoscroll)
        # Put the indicator to the right of each label (reads label-then-box).
        for cb in (self.errors_only, self.autoscroll):
            cb.setLayoutDirection(Qt.RightToLeft)
        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.clicked.connect(self.clear)
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.errors_only)
        header.addWidget(self.autoscroll)
        header.addWidget(clear_btn)

        self.view = QtWidgets.QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setObjectName("logView")
        self.view.setMaximumBlockCount(_MAX_LINES)
        font = QtGui.QFont("monospace")
        font.setStyleHint(QtGui.QFont.Monospace)
        font.setPointSize(9)
        self.view.setFont(font)

        lay.addLayout(header)
        lay.addWidget(self.view)

    def append_line(self, line: str) -> None:
        cat = self._classifier.classify(line)
        self._buffer.append((line, cat))
        # Paused (autoscroll off): keep recording, but leave the view frozen so the
        # operator can read it without it moving. It catches up when re-checked.
        if not self.autoscroll.isChecked():
            return
        if self.errors_only.isChecked() and cat is None:
            return
        self._append_html(line, cat)
        self._scroll_to_bottom()

    def _append_html(self, line: str, cat: str | None) -> None:
        safe = html.escape(line)
        if cat:
            safe = f"<span style='color:#e06c75'>{safe}</span>"
        self.view.appendHtml(safe)

    def _scroll_to_bottom(self) -> None:
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _on_autoscroll(self, checked: bool) -> None:
        # Re-checking catches the view up with everything buffered while paused.
        # Unchecking does nothing here, which is what freezes the current view.
        if checked:
            self._rerender()

    def _rerender(self) -> None:
        self.view.clear()
        only = self.errors_only.isChecked()
        for line, cat in self._buffer:
            if only and cat is None:
                continue
            self._append_html(line, cat)
        if self.autoscroll.isChecked():
            self._scroll_to_bottom()

    def clear(self) -> None:
        self._buffer.clear()
        self.view.clear()
