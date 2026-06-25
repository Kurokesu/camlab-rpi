"""Signal-integrity / error surfacing.

libcamera (and its IPA proxy child) log to stderr. We splice fd 2 onto a pipe so
we can (a) re-emit every byte to the real stderr -> journald (nothing lost) and
(b) feed each line through a classifier. Matched lines (e.g. AR0822 embedded-data
parse failures from a marginal CSI cable) are split by severity (error vs
warning) into two running counts the status strip surfaces as FACTs (no pass/fail
verdict, per spec).

fd splicing must happen before Picamera2()/libcamera init so the IPA child
inherits the redirected fd.
"""

from __future__ import annotations

import collections
import os
import re
import threading
from dataclasses import dataclass, field

from .qt import QtCore, Signal

# category -> regex. Editable data, not logic. Order matters (first match wins).
DEFAULT_PATTERNS: dict[str, str] = {
    "embedded_data": r"Embedded data buffer parsing failed",
    "register_tags": r"Incorrect register value tags",
    "csi_crc": r"\bCRC\b|corrupt(ed)? (frame|buffer)|pixel error",
    "frame_timeout": r"(?i)\b(timed out|timeout)\b|Dequeue timer|no buffers",
    "frame_drop": r"(?i)dropp(ed|ing) (a )?frame|frame drop",
    "v4l2_error": r"(?i)\bVIDIOC_\w+ failed|Failed to queue buffer|Failed to start",
}

# Human labels for the categories above.
CATEGORY_LABELS: dict[str, str] = {
    "embedded_data": "Embedded-data parse",
    "register_tags": "Register-tag mismatch",
    "csi_crc": "CSI CRC / corruption",
    "frame_timeout": "Frame timeout",
    "frame_drop": "Dropped frame",
    "v4l2_error": "V4L2 error",
}

# Severity fallback per category, used only when a matched line carries no
# explicit libcamera level token. Corruption / driver failures are errors.
# Transient per-frame hiccups are warnings.
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


def severity_for(line: str, category: str) -> str:
    """'error' or 'warning' for a matched line.

    Prefer libcamera's own level word. Fall back to the category default when a
    matched line has none.
    """
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
        """(category, severity) for a line, or (None, None) if it matches nothing."""
        cat = self.classify(line)
        if cat is None:
            return None, None
        return cat, severity_for(line, cat)


@dataclass
class IntegrityStats:
    errors: int = 0
    warnings: int = 0
    by_category: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.errors + self.warnings

    @property
    def healthy(self) -> bool:
        return self.errors == 0 and self.warnings == 0


class NullCapture(QtCore.QObject):
    """Drop-in that does no fd splicing (debug: CAMTEST_NO_CAPTURE=1)."""

    line_received = Signal(str)

    def stop(self) -> None:
        pass


class StderrCapture(QtCore.QObject):
    """Splices fd 2 onto a pipe, emits each captured line, mirrors to real stderr."""

    line_received = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
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
                try:  # keep journald copy
                    os.write(self._orig_fd, chunk)
                except OSError:
                    pass
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    self.line_received.emit(raw.decode("utf-8", "replace"))
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

    stats_changed = Signal(object)   # IntegrityStats
    matched = Signal(str, str)       # (category, line) for matched lines
    # NB: do NOT name a signal 'event' - it shadows QObject.event() and aborts.

    def __init__(self, classifier: LogClassifier | None = None,
                 emit_hz: float = 4.0, parent=None):
        super().__init__(parent)
        self._classifier = classifier or LogClassifier()
        self._errors = 0
        self._warnings = 0
        self._by_cat: collections.Counter = collections.Counter()
        self._dirty = False
        # Coalesce bursts: feed() runs on the capture thread for every matched
        # line. A timer publishes the rolled-up counts only when they changed.
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
        self.matched.emit(cat, line)

    def reset(self) -> None:
        self._errors = 0
        self._warnings = 0
        self._by_cat.clear()
        self._dirty = True

    def _emit(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        self.stats_changed.emit(IntegrityStats(
            errors=self._errors,
            warnings=self._warnings,
            by_category=dict(self._by_cat),
        ))
