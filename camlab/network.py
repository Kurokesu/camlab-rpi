# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Networking state for the Settings dialog.

Reading is unprivileged. Writing shells out to camlabctl net via sudo
(scoped in deploy/camlab-sudoers), which owns the mechanics: mask/unmask
plus persistence on a read-only box.
"""

from __future__ import annotations

import subprocess

CAMLABCTL = "/usr/local/bin/camlabctl"

# Networking is "on" when any manager runs. NetworkManager on stock RPi OS,
# systemd-networkd covers alternative setups.
_MANAGERS = ("NetworkManager.service", "systemd-networkd.service")


def is_enabled() -> bool:
    for unit in _MANAGERS:
        active = (
            subprocess.run(["systemctl", "is-active", "--quiet", unit], check=False).returncode == 0
        )
        if active:
            return True
    return False


def set_enabled(enabled: bool) -> None:
    """Toggle networking now (no reboot). Raises CalledProcessError on failure."""
    subprocess.run(["sudo", CAMLABCTL, "net", "on" if enabled else "off"], check=True)
