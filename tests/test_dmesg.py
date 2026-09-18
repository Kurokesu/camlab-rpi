# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Ring buffer scrape: which lines a failing sensor probe contributes, and as what."""

from __future__ import annotations

import pytest
from conftest import CAMERA_STACK, DMESG_SAMPLE, FAILURES, PROBE_FAILURE, SUBDEVICE_NOTICE

from camlab import dmesg
from camlab.integrity import LogClassifier


@pytest.fixture
def classifier() -> LogClassifier:
    return LogClassifier()


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


@pytest.mark.parametrize("line", FAILURES)
def test_driver_lines_classify_as_errors(classifier, line):
    assert classifier.classify_with_severity(line) == ("kernel_driver", "error")


def test_short_i2c_address_is_scraped_and_classified(classifier):
    """Scrape and classifier share the address shape, so a short one counts as well as shows."""
    line = "[    4.19] ar0822 10-10: probe with driver ar0822 failed with error -121"
    assert dmesg.driver_lines(f"kern  :err   : {line}\n", "ar0822") == [line]
    assert classifier.classify_with_severity(line) == ("kernel_driver", "error")


def test_subdevice_notice_is_context_not_error(classifier):
    """It shares the device prefix but reports success, so it must not tint or count."""
    assert SUBDEVICE_NOTICE in dmesg.driver_lines(DMESG_SAMPLE, "ar0822")
    assert classifier.classify_with_severity(SUBDEVICE_NOTICE) == (None, None)


@pytest.mark.parametrize("line", CAMERA_STACK)
def test_device_prefix_leaves_camera_stack_lines_alone(classifier, line):
    """Broadest pattern goes last, so nothing the capture already carries reads as a driver."""
    assert classifier.classify_with_severity(line)[0] != "kernel_driver"


def test_unknown_module_reads_nothing():
    """Exercises the real dmesg call, which is unprivileged while dmesg_restrict is 0."""
    assert dmesg.read("nosuchsensor") == []
