# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""apt-packages feeds install.sh, deps.sh and debian/rules, all shell, so a typo
would surface at install or deb-build time. This validates it on main."""

import re
import subprocess

from camlab.stack import pins

FORKS = ("LIBCAMERA", "RPICAM_APPS")


def app_packages() -> list[str]:
    return pins()["APP_PACKAGES"].split()


def dpkg_says(left: str, op: str, right: str) -> bool:
    cmd = ["dpkg", "--compare-versions", left, op, right]
    return subprocess.run(cmd, capture_output=True, check=False).returncode == 0


def test_os_codename_is_well_formed():
    """Blank or malformed, the deb's preinst refuses every normal system."""
    assert re.fullmatch(r"[a-z]+", pins()["OS_CODENAME"])


def test_both_forks_pin_version_and_packages():
    env = pins()
    for fork in FORKS:
        assert env[f"{fork}_VERSION"]
        assert env[f"{fork}_PACKAGES"].split()


def test_versions_carry_the_fork_epoch():
    """An unepoched floor sorts below every fork build and pins nothing."""
    env = pins()
    for fork in FORKS:
        assert ":" in env[f"{fork}_VERSION"], fork


def test_ceiling_admits_a_rebuild_and_stops_the_next_fork():
    """Trailing dot carries the whole ceiling, so prove it against real dpkg."""
    floor = pins()["LIBCAMERA_VERSION"]
    next_fork = re.sub(r"\d+$", lambda m: str(int(m.group()) + 1), floor)
    assert dpkg_says(f"{floor}-9", "lt", f"{floor}.")
    assert dpkg_says(next_fork, "ge", f"{floor}.")


def test_picamera2_is_recorded():
    env = pins()
    assert env["PICAMERA2_VERSION"]
    assert env["PICAMERA2_PACKAGES"].split()


def test_picamera2_is_named_once():
    """deps.sh installs it off PICAMERA2_PACKAGES, so APP_PACKAGES must not repeat it."""
    assert set(pins()["PICAMERA2_PACKAGES"].split()).isdisjoint(app_packages())


def test_app_packages_leave_fork_packages_to_the_pin():
    """A presence-checked fork package would land outside the pinned range."""
    forked = {pkg for fork in FORKS for pkg in pins()[f"{fork}_PACKAGES"].split()}
    assert forked.isdisjoint(app_packages())


def test_app_packages_are_bare_names():
    """Rendered straight into deb Depends, so a stray comma or range breaks it."""
    for pkg in app_packages():
        assert re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", pkg), pkg
