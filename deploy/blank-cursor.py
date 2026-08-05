#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Write a transparent Xcursor theme.

Cage paints a pointer at screen centre from its own startup until a client
overrides it, which shows an arrow on black boot screen. Pointed at by
XCURSOR_PATH, this theme makes that pointer invisible. A missing theme does not
work, wlroots then falls back to a builtin arrow.

Usage: blank-cursor.py <theme-root>
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

_IMAGE_TYPE = 0xFFFD0002
# Nominal sizes a compositor may ask for. One transparent pixel serves them all.
_SIZES = (24, 32, 48, 64)
# Cage requests left_ptr, wlroots maps CSS names onto default.
_NAMES = ("left_ptr", "default")


def _image(size: int) -> bytes:
    header = struct.pack("<9I", 36, _IMAGE_TYPE, size, 1, 1, 1, 0, 0, 0)
    return header + struct.pack("<I", 0)  # 1x1 ARGB, fully transparent


def _cursor_file() -> bytes:
    images = [_image(s) for s in _SIZES]
    header = struct.pack("<4sIII", b"Xcur", 16, 0x00010000, len(images))
    offset = len(header) + 12 * len(images)
    toc = b""
    for size, image in zip(_SIZES, images, strict=True):
        toc += struct.pack("<3I", _IMAGE_TYPE, size, offset)
        offset += len(image)
    return header + toc + b"".join(images)


def main(root: Path) -> None:
    cursors = root / "default" / "cursors"
    cursors.mkdir(parents=True, exist_ok=True)
    data = _cursor_file()
    for name in _NAMES:
        (cursors / name).write_bytes(data)
    (root / "default" / "index.theme").write_text("[Icon Theme]\nName=camlab-blank\n")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: blank-cursor.py <theme-root>")
    main(Path(sys.argv[1]))
