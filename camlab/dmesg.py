# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Kernel ring buffer scrape: driver failures behind a camera libcamera cannot see."""

from __future__ import annotations

import re
import subprocess

from .integrity import I2C_CLIENT, SUBDEVICE

# dmesg -x decorates every line as "facility:level : body"
_DECORATED = re.compile(r"^\w+ *:(\w+) *: (.*)")
_LOUD = frozenset({"emerg", "alert", "crit", "err", "warn"})
# A retrying driver fills the ring buffer, cap it so the scrape cannot drown the log stream
_MAX_LINES = 40


def driver_lines(text: str, module: str) -> list[str]:
    """Probe failures for one sensor module out of dmesg -x output."""
    device = re.compile(rf"(?:^|] ){re.escape(module)} {I2C_CLIENT}:")
    out = []
    for raw in text.splitlines():
        m = _DECORATED.match(raw)
        if not m:
            continue
        level, body = m.groups()
        # Subdevice notice is scraped as context, it separates bad overlay from bad wiring
        if (level in _LOUD and device.search(body)) or (SUBDEVICE in body and module in body):
            out.append(body)
    return out[:_MAX_LINES]


def read(module: str) -> list[str]:
    """Scrape the ring buffer once, empty when dmesg is unreadable."""
    try:
        proc = subprocess.run(
            ["dmesg", "-x"], capture_output=True, text=True, check=False, timeout=2
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    return driver_lines(proc.stdout, module)
