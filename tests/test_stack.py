# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Runtime pairing check: what counts as drift, and how the drift line classifies."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import logged

from camlab import dmesg, stack
from camlab.integrity import IntegrityStats, LogClassifier, breakdown_text

# What Picamera2.camera_manager.version reports, and the pin it pairs with
LOADED = "v0.7.2+rpt20260817+krks3"
PINNED = "1:0.7.2+rpt20260817+krks3"


@pytest.fixture
def pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Point the check at a pin file holding the given assignments."""

    def write(**env: str) -> None:
        path = tmp_path / "apt-packages"
        path.write_text("".join(f'{key}="{value}"\n' for key, value in env.items()))
        monkeypatch.setattr(stack, "PIN", path)

    return write


def test_paired_stack_says_nothing(pin):
    pin(LIBCAMERA_VERSION=PINNED, PICAMERA2_VERSION=stack._picamera2())
    assert stack.mismatches(LOADED) == []


def test_source_build_names_drift(pin):
    """What /usr/local defeats every dpkg check with."""
    pin(LIBCAMERA_VERSION=PINNED, PICAMERA2_VERSION=stack._picamera2())
    assert stack.mismatches("v0.7.3") == [
        "camera stack: libcamera 0.7.3, validated against 0.7.2+rpt20260817+krks3"
    ]


def test_picamera2_drift_names_loaded_version(pin):
    pin(LIBCAMERA_VERSION=PINNED, PICAMERA2_VERSION="0.3.99")
    assert stack.mismatches(LOADED) == [
        f"camera stack: picamera2 {stack._picamera2()}, validated against 0.3.99"
    ]


def test_debian_rebuild_still_pairs(pin):
    """Pin ceiling admits a rebuild, which the loaded version never carries."""
    pin(LIBCAMERA_VERSION=f"{PINNED}-2")
    assert stack.mismatches(LOADED) == []


def test_missing_pin_file_says_nothing(tmp_path, monkeypatch):
    """An install predating the pin file has nothing to pair against."""
    monkeypatch.setattr(stack, "PIN", tmp_path / "absent")
    assert stack.mismatches("v0.0.1") == []


def test_component_the_pin_omits_is_skipped(pin):
    """A deployed tree can carry a pin file older than the code reading it."""
    pin(LIBCAMERA_VERSION=PINNED)
    assert stack.mismatches(LOADED) == []


def test_shipped_pin_file_pairs_with_its_own_version():
    """Parser and version trimming against the file that ships, not a fixture."""
    pinned = stack.pins()["LIBCAMERA_VERSION"]
    notes = stack.mismatches(f"v{stack._validated(pinned)}")
    assert [note for note in notes if "libcamera" in note] == []


def test_drift_line_classifies_as_warning(pin):
    """The trap: unclassified, a drift warning hides under the Warnings filter."""
    pin(LIBCAMERA_VERSION=PINNED)
    line = logged(stack.mismatches("v0.7.3")[0])
    assert LogClassifier(dmesg.PATTERNS).classify_with_severity(line) == (
        "stack_pairing",
        "warning",
    )


def test_warning_breakdown_names_category():
    stats = IntegrityStats(warnings=1, by_category={"stack_pairing": 1})
    assert "Stack pairing: 1" in breakdown_text(stats, "warning")
