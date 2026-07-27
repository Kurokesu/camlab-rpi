# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""DRM sysfs facts shared by display management and config validation.

Qt-free on purpose: config_manager runs as the camlab-apply CLI.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

DRM_ROOT = Path(os.environ.get("CAMLAB_DRM_ROOT", "/sys/class/drm"))


def connected_connectors() -> set[str]:
    """Connector names (HDMI-A-1, DSI-2, ...) whose DRM status is connected."""
    names = set()
    for status in DRM_ROOT.glob("card*-*/status"):
        try:
            if status.read_text().strip() == "connected":
                names.add(status.parent.name.split("-", 1)[1])
        except OSError:
            continue
    return names


def has_dsi_connector() -> bool:
    """True when a DSI connector exists (a panel overlay is bound).
    DSI has no hotplug detect, so this is static within a boot."""
    return any(DRM_ROOT.glob("card*-DSI-*"))


def dsi_blocked_ports() -> set[str]:
    """CSI ports claimed by a DSI display overlay: DSI-1 pairs with cam0
    (DISP0), DSI-2 with cam1 (DISP1). Reports overlay claims, not wiring,
    since DSI connectors read "connected" with no panel attached."""
    blocked = set()
    for status in DRM_ROOT.glob("card*-DSI-*/status"):
        try:
            if status.read_text().strip() != "connected":
                continue
            idx = int(status.parent.name.rsplit("-", 1)[1])
        except (OSError, ValueError):
            continue
        port = f"cam{idx - 1}"
        if port in ("cam0", "cam1"):
            blocked.add(port)
            log.info("%s is claimed by DSI connector %s", port, status.parent.name)
    return blocked
