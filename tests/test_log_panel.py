# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Log panel filter and tally against kernel driver lines."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")

from conftest import CAMERA_STACK, FAILURES, PROBE_FAILURE, logged

from camlab import dmesg, stack
from camlab.gui.log_panel import LogPanel
from camlab.integrity import IntegrityMonitor, LogClassifier
from camlab.qt import QtWidgets


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def panel(qapp) -> LogPanel:
    return LogPanel(LogClassifier(dmesg.PATTERNS))


def test_errors_filter_keeps_driver_lines(panel):
    """Failures survive the filter, the success notice scraped beside them does not."""
    for line in [*PROBE_FAILURE, CAMERA_STACK[0]]:
        panel.append_line(line)
    panel.filter.button("error").click()
    shown = panel.view.toPlainText().splitlines()
    # View renders HTML, which collapses the run of spaces dmesg pads timestamps with
    assert shown == [" ".join(raw.split()) for raw in FAILURES]


def test_unclassified_lines_hide_under_the_errors_filter(qapp):
    """The trap the widened pattern set exists for."""
    panel = LogPanel(LogClassifier())
    for line in PROBE_FAILURE:
        panel.append_line(line)
    panel.filter.button("error").click()
    assert panel.view.toPlainText() == ""


def test_warnings_filter_keeps_a_stack_drift_line(panel):
    """Where a drift warning has to show, and where an unclassified line never would."""
    line = logged(f"{stack.PREFIX} libcamera 0.7.3, validated against 0.7.2")
    panel.append_line(line)
    panel.filter.button("warning").click()
    assert panel.view.toPlainText() == line


def test_tally_counts_driver_errors(panel):
    """Fed the whole scrape, so a success notice inflating the count would show here."""
    monitor = IntegrityMonitor(LogClassifier(dmesg.PATTERNS))
    seen: list = []
    monitor.stats_changed.connect(seen.append)
    for line in PROBE_FAILURE:
        monitor.feed(line)
    monitor._emit()
    assert (seen[-1].errors, seen[-1].warnings) == (len(FAILURES), 0)
    panel.update_integrity(seen[-1])
    assert panel.filter.button("error").text() == f"Errors {len(FAILURES)}"
