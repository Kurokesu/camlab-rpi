# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Ring buffer scrape: which lines a failing sensor probe contributes, and as what."""

from __future__ import annotations

import pytest

from camlab import dmesg
from camlab.integrity import IntegrityStats, LogClassifier, breakdown_text

# dmesg -x after an ar0822 probe failure, device-tree chatter and a foreign driver included
SAMPLE = """\
kern  :info  : [    0.036267] /axi/pcie@1000120000/rp1/i2c@88000/ar0822@10: Fixed dependency cycle(s) with /axi/pcie@1000120000/rp1/csi@110000
kern  :info  : [    0.036292] /axi/pcie@1000120000/rp1/csi@110000: Fixed dependency cycle(s) with /axi/pcie@1000120000/rp1/i2c@88000/ar0822@10
kern  :info  : [    4.037284] rp1-cfe 1f00110000.csi: found subdevice /axi/pcie@1000120000/rp1/i2c@88000/ar0822@10
kern  :warn  : [    4.100000] ar0822: loading out-of-tree module taints kernel.
kern  :err   : [    4.193195] ar0822 10-0010: Error reading reg 0x3000: -121
kern  :err   : [    4.193200] ar0822 10-0010: error -EREMOTEIO: Failed to read chip version
kern  :err   : [    4.199618] ar0822 10-0010: probe with driver ar0822 failed with error -121
kern  :err   : [    4.597354] edt_ft5x06 11-0038: Unable to fetch data, error: -121
"""

PROBE_FAILURE = [
    "[    4.037284] rp1-cfe 1f00110000.csi: found subdevice /axi/pcie@1000120000/rp1/i2c@88000/ar0822@10",
    "[    4.193195] ar0822 10-0010: Error reading reg 0x3000: -121",
    "[    4.193200] ar0822 10-0010: error -EREMOTEIO: Failed to read chip version",
    "[    4.199618] ar0822 10-0010: probe with driver ar0822 failed with error -121",
]

# The scrape carries the success notice for context, so only the rest are errors
SUBDEVICE_NOTICE = PROBE_FAILURE[0]
FAILURES = PROBE_FAILURE[1:]

# Lines the stderr capture already carries, none of them a kernel driver line
CAMERA_STACK = [
    "[0:32:24.379907628] [27725]  INFO Camera camera_manager.cpp:340 libcamera v0.7.1",
    "[2:03:04.000] [42] WARN V4L2 v4l2_videodevice.cpp:1906 /dev/video0[16:cap]: Dequeue timer",
    "00:00:00.059 [ERROR] [EGL] command: eglQueryDeviceStringEXT, error: EGL_BAD_PARAMETER",
    "00:00:00.328 [ERROR] [backend/drm/util.c:65] Failed to parse EDID",
    "12:50:03 ERROR camlab.camera: opened",
]


@pytest.fixture
def classifier() -> LogClassifier:
    return LogClassifier(dmesg.PATTERNS)


def test_filter_keeps_probe_failure_only():
    assert dmesg.driver_lines(SAMPLE, "ar0822") == PROBE_FAILURE


def test_filter_ignores_other_modules():
    assert dmesg.driver_lines(SAMPLE, "imx477") == []
    assert dmesg.driver_lines(SAMPLE, "edt_ft5x06") == [
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


def test_subdevice_notice_is_context_not_an_error(classifier):
    """It shares the device prefix but reports success, so it must not tint or count."""
    assert SUBDEVICE_NOTICE in dmesg.driver_lines(SAMPLE, "ar0822")
    assert classifier.classify_with_severity(SUBDEVICE_NOTICE) == (None, None)


@pytest.mark.parametrize("line", CAMERA_STACK)
def test_widening_leaves_camera_stack_lines_alone(classifier, line):
    assert classifier.classify_with_severity(line) == LogClassifier().classify_with_severity(line)


def test_error_breakdown_names_the_category():
    stats = IntegrityStats(errors=3, by_category={dmesg.CATEGORY: 3})
    assert "Kernel driver: 3" in breakdown_text(stats, "error")


def test_unknown_module_reads_nothing():
    """Exercises the real dmesg call, which is unprivileged while dmesg_restrict is 0."""
    assert dmesg.read("nosuchsensor") == []
