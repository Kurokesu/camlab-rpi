# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Driver table registry prints for drivers.sh and deb Depends."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from camlab.sensors import SensorRegistry

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_cli_prints_overlay_and_package_for_every_driver_sensor():
    out = subprocess.run(
        [sys.executable, "-m", "camlab.sensors"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    expected = [
        f"{s.overlay}\t{s.driver_package}" for s in SensorRegistry.load() if s.driver_package
    ]
    assert expected
    assert out.splitlines() == expected
