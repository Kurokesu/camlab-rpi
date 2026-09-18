# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Signal-integrity / error surfacing.

fd 2 is spliced onto a pipe, so each line is classified on its way to journald.
Splice before Picamera2()/libcamera init so the IPA child inherits the fd.
"""

from __future__ import annotations

import collections
import os
import re
import threading
from dataclasses import dataclass, field

from .qt import QtCore, Signal

# Own records: _setup_logging formats them, the regexes below parse them back.
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
LOG_DATEFMT = "%H:%M:%S"
_APP_STAMP = r"^\d\d:\d\d:\d\d "
_APP_LEVEL_RE = re.compile(_APP_STAMP + r"(DEBUG|INFO|WARNING|ERROR|CRITICAL) ")

# Kernel device prefix, "ar0822 10-0010:" on i2c and "rp1-cfe 1f00110000.csi:" on the platform bus
I2C_CLIENT = r"\d+-[0-9a-f]+"
# Subdevice notice shares the prefix but reports success, so it stays out of the category
SUBDEVICE = "found subdevice"
_KERNEL_DEVICE = rf"(?:^|] )[\w-]+ (?:{I2C_CLIENT}|[0-9a-f]+\.[\w-]+): (?!{SUBDEVICE})"


@dataclass(frozen=True)
class Category:
    name: str
    pattern: str
    severity: str = "warning"  # fallback when the line carries no level word
    tints_warning: bool = True  # errors always tint, this decides whether a warning does


# First match wins. Drift note leads because it is an own record that would read as app,
# and app leads the rest because an own record can quote libcamera's wording.
CATEGORIES: tuple[Category, ...] = (
    Category("stack_pairing", r"camera stack:"),
    Category("app", _APP_STAMP + r"(WARNING|ERROR|CRITICAL) ", tints_warning=False),
    Category("embedded_data", r"Embedded data buffer parsing failed", "error"),
    Category("register_tags", r"Incorrect register value tags", "error"),
    Category("csi_crc", r"\bCRC\b|corrupt(ed)? (frame|buffer)|pixel error", "error"),
    Category("frame_timeout", r"(?i)\b(timed out|timeout)\b|Dequeue timer|no buffers"),
    Category("frame_drop", r"(?i)dropp(ed|ing) (a )?frame|frame drop"),
    Category("v4l2_error", r"(?i)\bVIDIOC_\w+ failed|Failed to (queue buffer|start)", "error"),
    Category("kernel_driver", _KERNEL_DEVICE, "error"),
)

_TINTING = frozenset(cat.name for cat in CATEGORIES if cat.tints_warning)

# libcamera puts a level word mid-line ("... ERROR RPI ..."), own records lead with theirs
_LEVEL_RE = re.compile(r"\b(CRITICAL|ERROR|FATAL|WARN(?:ING)?)\b")

# journald parses a leading <N>. Syslog: 2 crit, 3 err, 4 warning, 6 info, 7 debug.
_APP_PRIORITY = {"CRITICAL": 2, "ERROR": 3, "WARNING": 4, "INFO": 6, "DEBUG": 7}
_SEVERITY_PRIORITY = {"error": 3, "warning": 4}
_INFO_PRIORITY = 6


def _severity(line: str, category: Category) -> str:
    """'error' or 'warning' for a line, from its own level word or the category fallback."""
    m = _LEVEL_RE.search(line)
    if m:
        return "warning" if m.group(1).startswith("WARN") else "error"
    return category.severity


class LogClassifier:
    def __init__(self):
        self._compiled = [(cat, re.compile(cat.pattern)) for cat in CATEGORIES]

    def classify_with_severity(self, line: str) -> tuple[str | None, str | None]:
        """(category, severity) for a line, or (None, None) when nothing matches."""
        for cat, rx in self._compiled:
            if rx.search(line):
                return cat.name, _severity(line, cat)
        return None, None


def journal_priority(line: str, classifier: LogClassifier) -> int:
    """Syslog priority for a captured line, own records first then camera stack.

    Anything else stays info, so compositor noise cannot flood journalctl -p err.
    """
    m = _APP_LEVEL_RE.match(line)
    if m:
        return _APP_PRIORITY[m.group(1)]
    _cat, sev = classifier.classify_with_severity(line)
    return _SEVERITY_PRIORITY.get(sev or "", _INFO_PRIORITY)


def mirror_lines(
    buf: bytes, classifier: LogClassifier, priorities: bool = True
) -> tuple[bytes, list[str], bytes]:
    """Take complete lines off buf: bytes for the mirror, decoded lines, remainder."""
    out = bytearray()
    lines: list[str] = []
    while b"\n" in buf:
        raw, buf = buf.split(b"\n", 1)
        line = raw.decode("utf-8", "replace")
        if priorities:
            out += b"<%d>" % journal_priority(line, classifier)
        out += raw + b"\n"
        lines.append(line)
    return bytes(out), lines, buf


@dataclass
class IntegrityStats:
    # severity -> category -> count. Tint needs the category behind a warning, not just a total
    by_severity: dict[str, dict[str, int]] = field(default_factory=dict)

    def total(self, severity: str) -> int:
        return sum(self.by_severity.get(severity, {}).values())


def tint_severity(stats: IntegrityStats) -> str:
    """Severity the Log button tints, '' for none.

    Every error tints. A warning tints only from a category flagged for it, so a standing
    amber cannot teach the operator to ignore the button.
    """
    if stats.total("error"):
        return "error"
    if any(cat in _TINTING for cat in stats.by_severity.get("warning", ())):
        return "warning"
    return ""


# Lines the log panel keeps, so a deeper backlog would never show
PANEL_LINES = 2000


class LineSource(QtCore.QObject):
    """Emits captured lines, holding what arrives before the log panel exists."""

    line_received = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._backlog: collections.deque[str] | None = collections.deque(maxlen=PANEL_LINES)
        # Capture thread appends while main thread drains, so no line goes out twice
        self._lock = threading.Lock()

    def deliver(self, line: str) -> None:
        with self._lock:
            if self._backlog is not None:
                self._backlog.append(line)
                return
        self.line_received.emit(line)

    def replay(self) -> None:
        """Emit backlog, then stop buffering. Qt drops a signal with nothing connected.

        Call on the main thread before the event loop runs, so a live line queued from the
        capture thread cannot reach the panel ahead of an older replayed one.
        """
        with self._lock:
            backlog, self._backlog = self._backlog, None
        for line in backlog or ():
            self.line_received.emit(line)

    def stop(self) -> None:
        pass


class NullCapture(LineSource):
    """Drop-in that does no fd splicing (debug: CAMLAB_NO_CAPTURE=1)."""


class StderrCapture(LineSource):
    """Splices fd 2 onto a pipe, emits each line, mirrors it with a priority prefix."""

    def __init__(self, classifier: LogClassifier, parent=None):
        super().__init__(parent)
        self._classifier = classifier
        self._orig_fd = os.dup(2)
        # journald consumes the <N> prefix, a terminal would just show it.
        self._priorities = not os.isatty(self._orig_fd)
        r, w = os.pipe()
        os.dup2(w, 2)
        os.close(w)
        self._read_fd = r
        self._running = True
        self._thread = threading.Thread(target=self._run, name="stderr-capture", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        buf = b""
        try:
            while self._running:
                chunk = os.read(self._read_fd, 4096)
                if not chunk:
                    break
                out, lines, buf = mirror_lines(buf + chunk, self._classifier, self._priorities)
                self._mirror(out)
                for line in lines:
                    self.deliver(line)
        except OSError:
            pass
        if buf:  # unterminated tail still belongs in the journal
            out, _lines, _rest = mirror_lines(buf + b"\n", self._classifier, self._priorities)
            self._mirror(out)

    def _mirror(self, data: bytes) -> None:
        """Copy to the real stderr, which is the journal stream under systemd."""
        if not data:
            return
        try:
            os.write(self._orig_fd, data)
        except OSError:
            pass

    def stop(self) -> None:
        self._running = False
        try:
            os.dup2(self._orig_fd, 2)  # restore real stderr
        except OSError:
            pass


class IntegrityMonitor(QtCore.QObject):
    """Consumes log lines, classifies integrity issues, emits rolling stats."""

    stats_changed = Signal(object)  # IntegrityStats
    # Never name a signal 'event', it shadows QObject.event() and aborts.

    def __init__(self, classifier: LogClassifier | None = None, emit_hz: float = 4.0, parent=None):
        super().__init__(parent)
        self._classifier = classifier or LogClassifier()
        self._tally: dict[str, collections.Counter] = {
            "error": collections.Counter(),
            "warning": collections.Counter(),
        }
        self._dirty = False
        # feed() runs on the capture thread. Timer publishes rolled-up counts only when
        # they changed, so bursts coalesce.
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(int(1000 / emit_hz))
        self._timer.timeout.connect(self._emit)
        self._timer.start()

    def feed(self, line: str) -> None:
        cat, sev = self._classifier.classify_with_severity(line)
        if cat is None:
            return
        self._tally[sev][cat] += 1
        self._dirty = True

    def reset(self) -> None:
        for counts in self._tally.values():
            counts.clear()
        self._dirty = True

    def _emit(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        self.stats_changed.emit(
            IntegrityStats({sev: dict(counts) for sev, counts in self._tally.items()})
        )
