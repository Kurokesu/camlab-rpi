# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Syslog priorities on the journald mirror."""

from __future__ import annotations

import pytest

from camlab.integrity import LogClassifier, journal_priority, prefix_lines


@pytest.fixture
def classifier() -> LogClassifier:
    return LogClassifier()


@pytest.mark.parametrize(
    ("level", "priority"),
    [("CRITICAL", 2), ("ERROR", 3), ("WARNING", 4), ("INFO", 6), ("DEBUG", 7)],
)
def test_own_record_keeps_level(classifier, level, priority):
    assert journal_priority(f"12:50:03 {level} camlab.camera: opened", classifier) == priority


def test_camera_stack_error_maps_to_err(classifier):
    line = "[1:02:03.456] [42] ERROR RPI pisp.cpp:99 Embedded data buffer parsing failed"
    assert journal_priority(line, classifier) == 3


def test_camera_stack_warning_maps_to_warning(classifier):
    assert journal_priority("dropped frame 42", classifier) == 4


@pytest.mark.parametrize(
    "line",
    [
        "00:00:00.059 [ERROR] [EGL] command: eglQueryDeviceStringEXT, error: EGL_BAD_PARAMETER",
        "00:00:00.328 [ERROR] [backend/drm/util.c:65] Failed to parse EDID",
        "[0:32:24.379907628] [27725]  INFO Camera camera_manager.cpp:340 libcamera v0.7.1",
    ],
)
def test_foreign_line_stays_info(classifier, line):
    assert journal_priority(line, classifier) == 6


def test_every_line_gets_prefix(classifier):
    buf = b"12:50:03 ERROR camlab: boom\n12:50:04 INFO camlab: fine\n"
    out, lines, rest = prefix_lines(buf, classifier)
    assert out == b"<3>12:50:03 ERROR camlab: boom\n<6>12:50:04 INFO camlab: fine\n"
    assert lines == ["12:50:03 ERROR camlab: boom", "12:50:04 INFO camlab: fine"]
    assert rest == b""


def test_partial_tail_waits_for_newline(classifier):
    out, lines, rest = prefix_lines(b"12:50:03 INFO camlab: half", classifier)
    assert (out, lines, rest) == (b"", [], b"12:50:03 INFO camlab: half")


def test_split_line_keeps_level(classifier):
    _out, _lines, rest = prefix_lines(b"12:50:03 ERROR camlab: sp", classifier)
    out, lines, rest = prefix_lines(rest + b"lit\n", classifier)
    assert out == b"<3>12:50:03 ERROR camlab: split\n"
    assert lines == ["12:50:03 ERROR camlab: split"]
    assert rest == b""


def test_mirror_keeps_undecodable_bytes(classifier):
    out, lines, _rest = prefix_lines(b"\xff raw byte\n", classifier)
    assert out == b"<6>\xff raw byte\n"
    assert lines == ["\ufffd raw byte"]
