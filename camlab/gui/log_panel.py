# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Collapsible log panel - shows the captured camera-stack stderr stream.

Lines matching an integrity pattern are coloured by severity (errors red,
warnings orange) and can be isolated with a mutually-exclusive filter whose
warning and error segments double as the running counts, so warnings are not
buried when errors flood the stream. The panel keeps a bounded ring buffer so
the filter re-renders without re-tailing.

It is also the session-diagnostics home: boot-to-viewfinder time sits in the
header, and on the touch panel so do the load and RAM facts the status strip
has no room for.
"""

from __future__ import annotations

import collections
import html

from ..integrity import IntegrityStats, LogClassifier, breakdown_text
from ..qt import Qt, QtCore, QtGui, QtWidgets, Signal, Slot
from . import icons
from .rpi_stats import RpiStatsView, pad
from .widgets import SegmentedSelector

_MAX_LINES = 2000

_POINTER_EVENTS = frozenset(
    {
        QtCore.QEvent.Type.MouseButtonPress,
        QtCore.QEvent.Type.MouseButtonRelease,
        QtCore.QEvent.Type.MouseButtonDblClick,
        QtCore.QEvent.Type.MouseMove,
    }
)

# Log line colour per severity, shared with the filter glyphs.
_SEV_COLOR = {"error": "#e06c75", "warning": "#e5c07b"}
# A zero count is not an all-clear verdict, so it reads muted rather than green.
_SEV_IDLE = "#8a909b"
_HEADER_ICON_PX = 18


class LogPanel(QtWidgets.QWidget):
    # Clearing the view also resets the counts, so the two cannot disagree.
    cleared = Signal()

    def __init__(self, classifier: LogClassifier | None = None, parent=None):
        super().__init__(parent)
        self._classifier = classifier or LogClassifier()
        # (line, severity): severity is None for unmatched lines.
        self._buffer: collections.deque[tuple[str, str | None]] = collections.deque(
            maxlen=_MAX_LINES
        )
        self._filter = "all"
        self._compact = False
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
        self.boot_lbl.setToolTip("Time from power-on to the first captured frame.")

        self.filter = SegmentedSelector()
        self.filter.set_options(
            [("All", "all"), (pad("0", 3), "warning"), (pad("0", 3), "error")],
            current="all",
            stretch=False,
        )
        self.filter.changed.connect(self._on_filter)

        self.follow_btn = QtWidgets.QPushButton()
        self.follow_btn.setCheckable(True)
        self.follow_btn.setChecked(True)
        self.follow_btn.setIcon(icons.icon("vertical_align_bottom", _HEADER_ICON_PX))
        self.follow_btn.setIconSize(QtCore.QSize(_HEADER_ICON_PX, _HEADER_ICON_PX))
        self.follow_btn.setToolTip(
            "Follow new lines. Swipe or scroll up to freeze the view for "
            "inspection, new lines keep buffering and reappear on return."
        )
        self.follow_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.follow_btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.follow_btn.toggled.connect(self._on_follow)

        clear_btn = QtWidgets.QPushButton("Clear")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self.clear)

        header.addWidget(title)
        header.addWidget(self.boot_lbl)
        header.addStretch(1)
        header.addWidget(self.filter)
        header.addWidget(self.follow_btn)
        header.addWidget(clear_btn)

        # Load and RAM on their own row, compact only: the status strip keeps
        # temperatures and has no width left for these.
        self.rpi = RpiStatsView(fields=("cpu", "gpu", "ram"), parent=self)
        self._rpi_row = QtWidgets.QWidget(self)
        hrow = QtWidgets.QHBoxLayout(self._rpi_row)
        hrow.setContentsMargins(12, 0, 12, 4)
        hrow.addWidget(self.rpi)
        hrow.addStretch(1)
        self._rpi_row.setVisible(False)

        self.view = QtWidgets.QPlainTextEdit()
        self.view.setReadOnly(True)
        self.view.setObjectName("logView")
        self.view.setMaximumBlockCount(_MAX_LINES)
        font = QtGui.QFont("monospace")
        font.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        font.setPointSize(9)
        self.view.setFont(font)
        # Kinetic touch scrolling. grabGesture also sets WA_AcceptTouchEvents
        # on the viewport, which stops Qt synthesizing mouse events from
        # finger drags (those would drag-select text instead).
        viewport = self.view.viewport()
        QtWidgets.QScroller.grabGesture(
            viewport, QtWidgets.QScroller.ScrollerGestureType.TouchGesture
        )
        scroller = QtWidgets.QScroller.scroller(viewport)
        props = scroller.scrollerProperties()
        props.setScrollMetric(
            QtWidgets.QScrollerProperties.ScrollMetric.HorizontalOvershootPolicy,
            QtWidgets.QScrollerProperties.OvershootPolicy.OvershootAlwaysOff,
        )
        scroller.setScrollerProperties(props)
        viewport.installEventFilter(self)
        self.view.verticalScrollBar().valueChanged.connect(self._on_scrolled)

        lay.addLayout(header)
        lay.addWidget(self._rpi_row)
        lay.addWidget(self.view)

        self.set_boot_time(None)
        self.update_integrity(IntegrityStats())

    def eventFilter(self, obj, ev) -> bool:
        """Drop mouse events Qt synthesizes from finger drags.

        The viewport takes the touch stream (what QScroller scrolls on), but
        Qt posts a synthesized mouse stream alongside it, which the text edit
        reads as a selection drag. A real mouse still selects.
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

    def set_rpi_stats(self, s) -> None:
        self.rpi.set_stats(s)
        self.rpi.setVisible(self.rpi.has_data)
        self._sync_rpi_row()

    def set_compact(self, compact: bool) -> None:
        self._compact = bool(compact)
        self._sync_rpi_row()

    def _sync_rpi_row(self) -> None:
        self._rpi_row.setVisible(self._compact and self.rpi.has_data)

    @Slot(object)
    def update_integrity(self, stats: IntegrityStats) -> None:
        """Counts ride the filter segments they select for."""
        for value, count in (("warning", stats.warnings), ("error", stats.errors)):
            color = _SEV_COLOR[value] if count else _SEV_IDLE
            self.filter.set_option_label(
                value,
                text=pad(str(count), 3),
                icon=icons.icon(value, _HEADER_ICON_PX, color),
                tooltip=breakdown_text(stats, value),
            )

    # log stream
    def append_line(self, line: str) -> None:
        _cat, sev = self._classifier.classify_with_severity(line)
        self._buffer.append((line, sev))
        # Frozen: keep recording, but leave the view still so the operator can
        # read it. It catches up when following resumes.
        if not self.follow_btn.isChecked():
            self._pending = True
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
        at_bottom = value >= self.view.verticalScrollBar().maximum() - 2
        if at_bottom != self.follow_btn.isChecked():
            self.follow_btn.setChecked(at_bottom)

    def _on_follow(self, checked: bool) -> None:
        # Re-rendering catches up with everything buffered while frozen, but
        # only pay for it when lines actually arrived.
        if not checked:
            return
        if self._pending:
            self._rerender()
        else:
            self._scroll_to_bottom()

    def _rerender(self) -> None:
        self._syncing = True
        self.view.clear()
        for line, sev in self._buffer:
            if self._passes(sev):
                self._append_html(line, sev)
        self._syncing = False
        self._pending = False
        if self.follow_btn.isChecked():
            self._scroll_to_bottom()

    def clear(self) -> None:
        self._buffer.clear()
        self._pending = False
        self._syncing = True
        self.view.clear()
        self._syncing = False
        self.cleared.emit()
