# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Log panel filter and tally against kernel driver, camera stack and own records."""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("PyQt6")

from conftest import CAMERA_STACK, FAILURES, PROBE_FAILURE, logged

from camlab import stack
from camlab.gui.log_panel import LogPanel
from camlab.integrity import IntegrityMonitor, IntegrityStats, LineSource

CAMERA_OPEN_FAILED = logged("camera open failed: no cameras available", logging.ERROR)


@pytest.fixture
def panel(qapp) -> LogPanel:
    return LogPanel()


def tallied(*lines: str) -> IntegrityStats:
    """Stats the monitor publishes for these lines, empty when none classified."""
    monitor = IntegrityMonitor()
    seen: list[IntegrityStats] = []
    monitor.stats_changed.connect(seen.append)
    for line in lines:
        monitor.feed(line)
    monitor._emit()
    return seen[-1] if seen else IntegrityStats()


def test_errors_filter_keeps_driver_lines(panel):
    """Failures survive the filter, the success notice scraped beside them does not."""
    for line in [*PROBE_FAILURE, CAMERA_STACK[0]]:
        panel.append_line(line)
    panel.filter.button("error").click()
    shown = panel.view.toPlainText().splitlines()
    # View renders HTML, which collapses the run of spaces dmesg pads timestamps with
    assert shown == [" ".join(raw.split()) for raw in FAILURES]


def test_warnings_filter_keeps_stack_drift_line(panel):
    """Where a drift warning has to show, and where an unclassified line never would."""
    line = logged(f"{stack.PREFIX} libcamera 0.7.3, validated against 0.7.2")
    panel.append_line(line)
    panel.filter.button("warning").click()
    assert panel.view.toPlainText() == line


def test_replayed_and_live_lines_pass_same_classification(panel):
    """Drift warning captured before replay lands beside one captured after, both warnings."""
    source = LineSource()
    early = logged(f"{stack.PREFIX} libcamera 0.7.3, validated against 0.7.2")
    late = logged(f"{stack.PREFIX} picamera2 0.3.31, validated against 0.3.30")
    source.line_received.connect(panel.append_line)
    source.deliver(early)
    assert panel.view.toPlainText() == ""
    source.replay()
    source.deliver(late)
    panel.filter.button("warning").click()
    assert panel.view.toPlainText().splitlines() == [early, late]


def test_tally_counts_driver_errors(panel):
    """Fed the whole scrape, so a success notice inflating the count would show here."""
    stats = tallied(*PROBE_FAILURE)
    assert (stats.total("error"), stats.total("warning")) == (len(FAILURES), 0)
    panel.update_integrity(stats)
    assert panel.filter.button("error").text() == f"Errors {len(FAILURES)}"


def test_camera_open_failure_reaches_errors_filter(panel):
    """Worst failure an operator meets, so the Errors filter has to keep it."""
    panel.append_line(CAMERA_OPEN_FAILED)
    panel.filter.button("error").click()
    assert panel.view.toPlainText() == CAMERA_OPEN_FAILED


@pytest.mark.parametrize(
    ("line", "severity", "label"),
    [
        (CAMERA_OPEN_FAILED, "error", "Errors 1"),
        (logged("settings schema mismatch - ignoring"), "warning", "Warnings 1"),
    ],
)
def test_own_record_counts_under_app(panel, line, severity, label):
    """Both severities count, and the category separates app warnings from stack warnings."""
    stats = tallied(line)
    assert stats.by_severity[severity] == {"app": 1}
    panel.update_integrity(stats)
    assert panel.filter.button(severity).text() == label


def test_info_record_neither_counts_nor_shows(panel):
    """Only WARNING and above classify, so routine chatter stays plain and uncounted."""
    line = logged("first frame at boot time=3.1s", logging.INFO)
    stats = tallied(line)
    panel.append_line(line)
    panel.filter.button("warning").click()
    assert (stats.total("error"), stats.total("warning")) == (0, 0)
    assert panel.view.toPlainText() == ""


def test_tally_follows_line_severity_not_category_default(qapp):
    """Frame timeout defaults to warning, so the same line at ERROR has to count as an error."""
    stats = tallied(CAMERA_STACK[1].replace("WARN", "ERROR"))
    assert stats.by_severity["error"] == {"frame_timeout": 1}
    assert stats.by_severity["warning"] == {}
