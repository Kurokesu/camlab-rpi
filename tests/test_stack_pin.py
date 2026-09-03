# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""camera-stack.version feeds deps.sh and debian/rules, both shell, so a typo
would surface at install or deb-build time. This validates it on main."""

import re
from pathlib import Path

PIN = Path(__file__).resolve().parent.parent / "camera-stack.version"


def pins() -> dict[str, str]:
    return dict(re.findall(r'^(\w+)="([^"]*)"$', PIN.read_text(), flags=re.MULTILINE))


def test_both_forks_pin_version_and_packages():
    env = pins()
    for fork in ("LIBCAMERA", "RPICAM_APPS"):
        assert env[f"{fork}_VERSION"]
        assert env[f"{fork}_PACKAGES"].split()


def test_versions_carry_the_fork_epoch():
    """An unepoched floor sorts below every fork build and pins nothing."""
    for key, value in pins().items():
        if key.endswith("_VERSION"):
            assert ":" in value, key
