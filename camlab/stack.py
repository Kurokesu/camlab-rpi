# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Runtime camera stack pairing check against apt-packages.

Reads what is loaded rather than what dpkg installed, so a libcamera built into
/usr/local reports its own version instead of passing as the pinned package.
rpicam-apps runs in no camlab process, so its pin stays an install-time check.
"""

from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

PIN = Path(__file__).resolve().parent.parent / "apt-packages"

# Lead the log panel keys on, so a drift line reaches the Warnings filter
PREFIX = "camera stack:"

_ASSIGN = re.compile(r'^(\w+)="([^"]*)"$', re.MULTILINE)


def sourced(path: Path) -> dict[str, str]:
    """Shell assignments, values taken whole so a list spanning lines survives."""
    return dict(_ASSIGN.findall(path.read_text()))


def pins() -> dict[str, str]:
    """Empty for an install predating the pin file."""
    return sourced(PIN) if PIN.is_file() else {}


def _validated(pinned: str) -> str:
    """Pin as a library reports itself, without epoch or Debian revision."""
    upstream = pinned.split(":", 1)[-1]
    return upstream.rpartition("-")[0] or upstream


def _picamera2() -> str:
    """Distribution metadata, the package exports no __version__."""
    try:
        return version("picamera2")
    except PackageNotFoundError:
        return "unknown"


def _below(loaded: str, floor: str) -> bool:
    """Dotted numeric order, an unreadable version counts as below."""
    try:
        return tuple(map(int, loaded.split("."))) < tuple(map(int, floor.split(".")))
    except ValueError:
        return True


def mismatches(libcamera_version: str) -> list[str]:
    """Warnings for components off the stack this release was validated against.

    libcamera_version comes from Picamera2.camera_manager.version. libcamera must
    match its pin, picamera2 only reach its floor, since Raspberry Pi moves it.
    """
    env = pins()
    notes = []
    # Keys absent where pin file is older than the component it would name
    if pinned := env.get("LIBCAMERA_VERSION"):
        loaded, want = libcamera_version.lstrip("v"), _validated(pinned)
        if loaded != want:
            notes.append(f"{PREFIX} libcamera {loaded}, validated against {want}")
    if floor := env.get("PICAMERA2_VERSION"):
        loaded = _picamera2()
        if _below(loaded, floor):
            notes.append(f"{PREFIX} picamera2 {loaded}, below floor {floor}")
    return notes
