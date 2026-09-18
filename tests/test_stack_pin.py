# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""apt-packages feeds install.sh, deps.sh and debian/rules, all shell, so a typo
would surface at install or deb-build time. This validates it on main."""

import re
import subprocess
from pathlib import Path

from camlab.stack import pins

FORKS = ("LIBCAMERA", "RPICAM_APPS")

COMMON = Path(__file__).resolve().parent.parent / "scripts" / "common.sh"

RELATION = re.compile(r"(\S+) \((>=|<<) (\S+)\)")


def app_packages() -> list[str]:
    return pins()["APP_PACKAGES"].split()


def dpkg_says(left: str, op: str, right: str) -> bool:
    cmd = ["dpkg", "--compare-versions", left, op, right]
    return subprocess.run(cmd, capture_output=True, check=False).returncode == 0


def next_fork(floor: str) -> str:
    """Fork build after floor, trailing counter bumped."""
    return re.sub(r"\d+$", lambda m: str(int(m.group()) + 1), floor)


def relation_bounds(*given: str) -> dict[str, dict[str, str]]:
    """Bounds stack_relations emits per package, keyed by apt operator."""
    # Pre-set owner, or common.sh resolves one off the running unit
    script = f'CAMLAB_USER=root; source "{COMMON}"; stack_relations "$@"'
    emitted = subprocess.run(
        ["bash", "-c", script, "bash", *given],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    bounds: dict[str, dict[str, str]] = {}
    for line in emitted.splitlines():
        relation = RELATION.fullmatch(line)
        assert relation, line
        pkg, op, version = relation.groups()
        bounds.setdefault(pkg, {})[op] = version
    return bounds


def test_os_codename_is_well_formed():
    """Blank or malformed, the deb's preinst refuses every normal system."""
    assert re.fullmatch(r"[a-z]+", pins()["OS_CODENAME"])


def test_both_forks_pin_version_and_packages():
    env = pins()
    for fork in FORKS:
        assert env[f"{fork}_VERSION"]
        assert env[f"{fork}_PACKAGES"].split()


def test_versions_carry_fork_epoch():
    """An unepoched floor sorts below every fork build and pins nothing."""
    env = pins()
    for fork in FORKS:
        assert ":" in env[f"{fork}_VERSION"], fork


def test_ceiling_admits_rebuild_and_stops_next_fork():
    """Trailing dot carries the whole ceiling, so prove it against real dpkg."""
    floor = pins()["LIBCAMERA_VERSION"]
    assert dpkg_says(f"{floor}-9", "lt", f"{floor}.")
    assert dpkg_says(next_fork(floor), "ge", f"{floor}.")


def test_stack_relations_admit_rebuild_and_stop_next_fork():
    """Relations reach apt as argv, so prove emitted bounds against real dpkg."""
    floor = "1:2.3.4+krks7"
    bounds = relation_bounds(f"{floor} pkg-a pkg-b")
    assert set(bounds) == {"pkg-a", "pkg-b"}
    for pkg, bound in bounds.items():
        assert set(bound) == {">=", "<<"}, pkg
        assert dpkg_says(floor, "ge", bound[">="]), pkg
        assert dpkg_says(f"{floor}-9", "lt", bound["<<"]), pkg
        assert dpkg_says(next_fork(floor), "ge", bound["<<"]), pkg


def test_stack_relations_cover_both_fork_pins():
    """Callers pass both pins in one call, so every pinned package needs a pair."""
    env = pins()
    given = [" ".join((env[f"{fork}_VERSION"], env[f"{fork}_PACKAGES"])) for fork in FORKS]
    wanted = {pkg for fork in FORKS for pkg in env[f"{fork}_PACKAGES"].split()}
    assert set(relation_bounds(*given)) == wanted


def test_picamera2_is_recorded():
    env = pins()
    assert env["PICAMERA2_VERSION"]
    assert env["PICAMERA2_PACKAGES"].split()


def test_picamera2_is_named_once():
    """deps.sh installs it off PICAMERA2_PACKAGES, so APP_PACKAGES must not repeat it."""
    assert set(pins()["PICAMERA2_PACKAGES"].split()).isdisjoint(app_packages())


def test_app_packages_leave_fork_packages_to_pin():
    """A presence-checked fork package would land outside the pinned range."""
    forked = {pkg for fork in FORKS for pkg in pins()[f"{fork}_PACKAGES"].split()}
    assert forked.isdisjoint(app_packages())


def test_app_packages_are_bare_names():
    """Rendered straight into deb Depends, so a stray comma or range breaks it."""
    for pkg in app_packages():
        assert re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", pkg), pkg
