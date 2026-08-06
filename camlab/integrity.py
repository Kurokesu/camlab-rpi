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

# category -> regex, order matters (first match wins).
DEFAULT_PATTERNS: dict[str, str] = {
    "embedded_data": r"Embedded data buffer parsing failed",
    "register_tags": r"Incorrect register value tags",
    "csi_crc": r"\bCRC\b|corrupt(ed)? (frame|buffer)|pixel error",
    "frame_timeout": r"(?i)\b(timed out|timeout)\b|Dequeue timer|no buffers",
    "frame_drop": r"(?i)dropp(ed|ing) (a )?frame|frame drop",
    "v4l2_error": r"(?i)\bVIDIOC_\w+ failed|Failed to queue buffer|Failed to start",
}

CATEGORY_LABELS: dict[str, str] = {
    "embedded_data": "Embedded-data parse",
    "register_tags": "Register-tag mismatch",
    "csi_crc": "CSI CRC / corruption",
    "frame_timeout": "Frame timeout",
    "frame_drop": "Dropped frame",
    "v4l2_error": "V4L2 error",
}

# Severity fallback, used when a matched line carries no libcamera level token.
CATEGORY_SEVERITY: dict[str, str] = {
    "embedded_data": "error",
    "register_tags": "error",
    "csi_crc": "error",
    "v4l2_error": "error",
    "frame_timeout": "warning",
    "frame_drop": "warning",
}

# libcamera prefixes each line with a level word (e.g. "... ERROR RPI ...").
_LEVEL_RE = re.compile(r"\b(ERROR|FATAL|WARN(?:ING)?)\b")

# Own records, as _setup_logging formats them: "HH:MM:SS LEVEL name: message".
_APP_LEVEL_RE = re.compile(r"^\d\d:\d\d:\d\d (DEBUG|INFO|WARNING|ERROR|CRITICAL) ")

# journald parses a leading <N>. Syslog: 2 crit, 3 err, 4 warning, 6 info, 7 debug.
_APP_PRIORITY = {"CRITICAL": 2, "ERROR": 3, "WARNING": 4, "INFO": 6, "DEBUG": 7}
_SEVERITY_PRIORITY = {"error": 3, "warning": 4}
_INFO_PRIORITY = 6


def severity_for(line: str, category: str) -> str:
    """'error' or 'warning' for a line, from libcamera's level word or category default."""
    m = _LEVEL_RE.search(line)
    if m:
        return "error" if m.group(1) in ("ERROR", "FATAL") else "warning"
    return CATEGORY_SEVERITY.get(category, "warning")


class LogClassifier:
    def __init__(self, patterns: dict[str, str] | None = None):
        pats = patterns or DEFAULT_PATTERNS
        self._compiled = [(cat, re.compile(rx)) for cat, rx in pats.items()]

    def classify(self, line: str) -> str | None:
        for cat, rx in self._compiled:
            if rx.search(line):
                return cat
        return None

    def classify_with_severity(self, line: str) -> tuple[str | None, str | None]:
        """(category, severity) for a line, or (None, None) when nothing matches."""
        cat = self.classify(line)
        if cat is None:
            return None, None
        return cat, severity_for(line, cat)


def journal_priority(line: str, classifier: LogClassifier) -> int:
    """Syslog priority for a captured line, own records first then camera stack.

    Anything else stays info, so compositor noise cannot flood journalctl -p err.
    """
    m = _APP_LEVEL_RE.match(line)
    if m:
        return _APP_PRIORITY[m.group(1)]
    _cat, sev = classifier.classify_with_severity(line)
    return _SEVERITY_PRIORITY.get(sev or "", _INFO_PRIORITY)


def prefix_lines(buf: bytes, classifier: LogClassifier) -> tuple[bytes, list[str], bytes]:
    """Take complete lines off buf: journald bytes with priorities, lines, remainder."""
    out = bytearray()
    lines: list[str] = []
    while b"\n" in buf:
        raw, buf = buf.split(b"\n", 1)
        line = raw.decode("utf-8", "replace")
        out += b"<%d>" % journal_priority(line, classifier) + raw + b"\n"
        lines.append(line)
    return bytes(out), lines, buf


@dataclass
class IntegrityStats:
    errors: int = 0
    warnings: int = 0
    by_category: dict[str, int] = field(default_factory=dict)


def breakdown_text(stats: IntegrityStats, severity: str) -> str:
    """Per-category tally for one severity, as tooltip text."""
    noun = "errors" if severity == "error" else "warnings"
    rows = [
        f"  {CATEGORY_LABELS.get(cat, cat)}: {n}"
        for cat, n in sorted(stats.by_category.items(), key=lambda kv: -kv[1])
        if n and CATEGORY_SEVERITY.get(cat) == severity
    ]
    if not rows:
        return f"No camera-stack {noun} observed."
    return f"Camera-stack {noun} (facts, not a verdict):\n" + "\n".join(rows)


class NullCapture(QtCore.QObject):
    """Drop-in that does no fd splicing (debug: CAMLAB_NO_CAPTURE=1)."""

    line_received = Signal(str)

    def stop(self) -> None:
        pass


class StderrCapture(QtCore.QObject):
    """Splices fd 2 onto a pipe, emits each line, mirrors it with a priority prefix."""

    line_received = Signal(str)

    def __init__(self, classifier: LogClassifier, parent=None):
        super().__init__(parent)
        self._classifier = classifier
        self._orig_fd = os.dup(2)
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
                out, lines, buf = prefix_lines(buf + chunk, self._classifier)
                self._mirror(out)
                for line in lines:
                    self.line_received.emit(line)
        except OSError:
            pass
        if buf:  # unterminated tail still belongs in the journal
            out, _lines, _rest = prefix_lines(buf + b"\n", self._classifier)
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
        self._errors = 0
        self._warnings = 0
        self._by_cat: collections.Counter = collections.Counter()
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
        if sev == "error":
            self._errors += 1
        else:
            self._warnings += 1
        self._by_cat[cat] += 1
        self._dirty = True

    def reset(self) -> None:
        self._errors = 0
        self._warnings = 0
        self._by_cat.clear()
        self._dirty = True

    def _emit(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        self.stats_changed.emit(
            IntegrityStats(
                errors=self._errors,
                warnings=self._warnings,
                by_category=dict(self._by_cat),
            )
        )
