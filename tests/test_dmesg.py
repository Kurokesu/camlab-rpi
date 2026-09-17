# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Ring buffer scrape: which lines a failing sensor probe contributes, and as what."""

from __future__ import annotations

import pytest
from conftest import CAMERA_STACK, DMESG_SAMPLE, FAILURES, PROBE_FAILURE, SUBDEVICE_NOTICE

from camlab import dmesg
from camlab.integrity import IntegrityStats, LogClassifier, breakdown_text


@pytest.fixture
def classifier() -> LogClassifier:
    return LogClassifier(dmesg.PATTERNS)


def test_filter_keeps_probe_failure_only():
    assert dmesg.driver_lines(DMESG_SAMPLE, "ar0822") == PROBE_FAILURE


def test_filter_ignores_other_modules():
    assert dmesg.driver_lines(DMESG_SAMPLE, "imx477") == []
    assert dmesg.driver_lines(DMESG_SAMPLE, "edt_ft5x06") == [
        "[    4.597354] edt_ft5x06 11-0038: Unable to fetch data, error: -121"
    ]


def test_retrying_driver_cannot_flood():
    spam = "".join(
        f"kern  :err   : [{i:>12.6f}] ar0822 10-0010: Error reading reg 0x3000: -121\n"
        for i in range(200)
    )
    assert len(dmesg.driver_lines(spam, "ar0822")) == 40


@pytest.mark.parametrize("line", PROBE_FAILURE)
def test_camera_stack_patterns_miss_driver_lines(line):
    """Why PATTERNS exists: unclassified lines drop out of the Errors filter and the tally."""
    assert LogClassifier().classify_with_severity(line) == (None, None)


@pytest.mark.parametrize("line", FAILURES)
def test_driver_lines_classify_as_errors(classifier, line):
    assert classifier.classify_with_severity(line) == (dmesg.CATEGORY, "error")


def test_subdevice_notice_is_context_not_error(classifier):
    """It shares the device prefix but reports success, so it must not tint or count."""
    assert SUBDEVICE_NOTICE in dmesg.driver_lines(DMESG_SAMPLE, "ar0822")
    assert classifier.classify_with_severity(SUBDEVICE_NOTICE) == (None, None)


@pytest.mark.parametrize("line", CAMERA_STACK)
def test_widening_leaves_camera_stack_lines_alone(classifier, line):
    assert classifier.classify_with_severity(line) == LogClassifier().classify_with_severity(line)


def test_error_breakdown_names_category():
    stats = IntegrityStats(errors=3, by_category={dmesg.CATEGORY: 3})
    assert "Kernel driver: 3" in breakdown_text(stats, "error")


def test_unknown_module_reads_nothing():
    """Exercises the real dmesg call, which is unprivileged while dmesg_restrict is 0."""
    assert dmesg.read("nosuchsensor") == []
