# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Component resolution, archive-origin gate, apt policy parsing and state file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from camlab import updater
from camlab.sensors import Sensor, SensorRegistry
from camlab.updater import Component, PackageState, UpdateError

ARCHIVE = "https://apt.kurokesu.com"

FROM_ARCHIVE = """\
camlab:
  Installed: 1.0.0
  Candidate: 1.0.1
  Version table:
     1.0.1 500
        500 https://apt.kurokesu.com trixie/main arm64 Packages
 *** 1.0.0 500
        500 https://apt.kurokesu.com trixie/main arm64 Packages
        100 /var/lib/dpkg/status
"""

HAND_INSTALLED = """\
camlab:
  Installed: 1.0.0+fork
  Candidate: 1.0.1
  Version table:
     1.0.1 500
        500 https://apt.kurokesu.com trixie/main arm64 Packages
 *** 1.0.0+fork 100
        100 /var/lib/dpkg/status
"""

NOT_INSTALLED = """\
camlab:
  Installed: (none)
  Candidate: 1.0.1
  Version table:
     1.0.1 500
        500 https://apt.kurokesu.com trixie/main arm64 Packages
"""


@pytest.fixture(autouse=True)
def pinned_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Archive URL is read from the environment at import, pin it for tests."""
    monkeypatch.setattr(updater, "ARCHIVE_URL", ARCHIVE)


@pytest.fixture
def registry() -> SensorRegistry:
    return SensorRegistry(
        [
            Sensor(name="IMX585", overlay="imx585", driver_package="imx585-rpi-dkms"),
            Sensor(name="AR0234", overlay="ar0234", driver_package="ar0234-rpi-dkms"),
            Sensor(name="OV5647", overlay="ov5647"),  # in-tree driver, no package
        ]
    )


@pytest.fixture
def policy(monkeypatch: pytest.MonkeyPatch):
    """Feed apt-cache policy output and treat any differing candidate as newer."""

    def feed(text: str) -> None:
        monkeypatch.setattr(updater, "_run", lambda cmd: text)
        monkeypatch.setattr(updater, "_newer", lambda cand, inst: cand != inst)

    return feed


@pytest.fixture
def inventory(monkeypatch: pytest.MonkeyPatch):
    """Stub the two package inventories components() builds on."""

    def build(served: set[str], installed: set[str]) -> None:
        monkeypatch.setattr(updater, "archive_packages", lambda: served)
        monkeypatch.setattr(updater, "installed_packages", lambda: installed)

    return build


class TestPolicy:
    def test_reads_installed_and_candidate(self, policy):
        policy(FROM_ARCHIVE)
        state = updater.package_states(["camlab"])["camlab"]
        assert (state.installed, state.candidate) == ("1.0.0", "1.0.1")

    def test_uninstalled_reads_as_none(self, policy):
        policy(NOT_INSTALLED)
        assert updater.package_states(["camlab"])["camlab"].installed is None

    def test_archive_version_is_flagged(self, policy):
        policy(FROM_ARCHIVE)
        assert updater.package_states(["camlab"])["camlab"].from_archive

    def test_hand_installed_version_is_not(self, policy):
        """Only dpkg offers it, so the archive did not put it there."""
        policy(HAND_INSTALLED)
        assert not updater.package_states(["camlab"])["camlab"].from_archive

    def test_newer_candidate_is_pending(self, policy):
        policy(FROM_ARCHIVE)
        assert updater.package_states(["camlab"])["camlab"].pending == "1.0.1"

    def test_same_version_is_not_pending(self, policy, monkeypatch):
        policy(FROM_ARCHIVE)
        monkeypatch.setattr(updater, "_newer", lambda cand, inst: False)
        assert updater.package_states(["camlab"])["camlab"].pending == ""

    def test_unknown_package_is_absent(self, policy):
        """apt-cache prints nothing for a name it does not know."""
        policy("")
        assert updater.package_states(["camlab"]) == {}

    def test_no_names_skips_apt(self, monkeypatch):
        monkeypatch.setattr(updater, "_run", lambda cmd: pytest.fail("apt-cache called"))
        assert updater.package_states([]) == {}


class TestArchivePackages:
    def test_reads_the_archive_index_only(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(updater, "APT_LISTS", tmp_path)
        (tmp_path / "apt.kurokesu.com_dists_trixie_main_binary-arm64_Packages").write_text(
            "Package: camlab\nVersion: 1.0.0\n\nPackage: libcamera0.7\nVersion: 1:0.7.1\n"
        )
        (tmp_path / "deb.debian.org_debian_dists_trixie_main_binary-arm64_Packages").write_text(
            "Package: coreutils\nVersion: 9.4\n"
        )
        assert updater.archive_packages() == {"camlab", "libcamera0.7"}

    def test_missing_index_reads_as_empty(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(updater, "APT_LISTS", tmp_path / "gone")
        assert updater.archive_packages() == set()


class TestComponents:
    def test_app_comes_first(self, registry, inventory):
        inventory({"camlab"}, {"camlab"})
        assert updater.components(registry)[0] == Component("app", "camlab", ("camlab",))

    def test_driver_ids_follow_the_registry(self, registry, inventory):
        inventory(set(), set())
        ids = [c.id for c in updater.components(registry)]
        assert ids == ["app", "driver:ar0234", "driver:imx585"]

    def test_sensor_without_a_package_gets_no_component(self, registry, inventory):
        inventory(set(), set())
        assert "driver:ov5647" not in [c.id for c in updater.components(registry)]

    def test_stack_takes_the_rest_of_the_archive(self, registry, inventory):
        inventory(
            {"camlab", "ar0234-rpi-dkms", "libcamera0.7", "python3-libcamera"},
            {"camlab", "ar0234-rpi-dkms", "libcamera0.7", "python3-libcamera", "coreutils"},
        )
        stack = [c for c in updater.components(registry) if c.id == "stack"]
        assert stack[0].packages == ("libcamera0.7", "python3-libcamera")

    def test_stack_skips_uninstalled_archive_packages(self, registry, inventory):
        inventory({"camlab", "libcamera0.7"}, {"camlab"})
        assert "stack" not in [c.id for c in updater.components(registry)]

    def test_resolve_maps_an_id_to_packages(self, registry, inventory):
        inventory(set(), set())
        assert updater.resolve("driver:ar0234", registry).packages == ("ar0234-rpi-dkms",)

    def test_resolve_rejects_anything_else(self, registry, inventory):
        """The shim takes ids, never package names, so an unknown id installs nothing."""
        inventory(set(), set())
        with pytest.raises(UpdateError, match="unknown component"):
            updater.resolve("libcamera0.7", registry)


class TestGate:
    def test_archive_install_gets_updates(self, policy):
        policy(FROM_ARCHIVE)
        assert updater.update_path() == ""

    def test_tarball_install_gets_none(self, policy):
        policy("")
        assert "not installed as a package" in updater.update_path()

    def test_foreign_build_gets_none(self, policy):
        policy(HAND_INSTALLED)
        assert ARCHIVE in updater.update_path()


class TestSurvey:
    @pytest.fixture(autouse=True)
    def one_pending_driver(self, monkeypatch, inventory):
        inventory(set(), set())
        states = {
            "camlab": PackageState("camlab", "1.0.0", "1.0.0", True, ""),
            "ar0234-rpi-dkms": PackageState("ar0234-rpi-dkms", "0.1.0", "0.2.0", True, "0.2.0"),
        }
        monkeypatch.setattr(updater, "package_states", lambda names: states)

    def test_pending_rolls_up_to_the_component(self, registry):
        by_id = {c["id"]: c for c in updater.survey(registry)["components"]}
        assert by_id["driver:ar0234"]["pending"] is True
        assert by_id["app"]["pending"] is False

    def test_uninstalled_component_is_left_out(self, registry):
        assert "driver:imx585" not in [c["id"] for c in updater.survey(registry)["components"]]


class TestStateFile:
    @pytest.fixture(autouse=True)
    def state_file(self, tmp_path: Path, monkeypatch) -> Path:
        path = tmp_path / "update.json"
        monkeypatch.setenv("CAMLAB_UPDATE_FILE", str(path))
        return path

    def test_round_trip(self, state_file: Path):
        updater.write_state({"version": 1, "checked": "2026-08-06T12:00:00+00:00"})
        assert updater.read_state()["checked"] == "2026-08-06T12:00:00+00:00"

    def test_never_checked_reads_as_empty(self):
        assert updater.read_state() == {}

    def test_corrupt_file_reads_as_empty(self, state_file: Path):
        state_file.write_text("{ half written")
        assert updater.read_state() == {}

    def test_schema_bump_reads_as_empty(self, state_file: Path):
        state_file.write_text(json.dumps({"version": 99, "checked": "yesterday"}))
        assert updater.read_state() == {}

    def test_gui_user_can_read_what_root_wrote(self, state_file: Path):
        updater.write_state({"version": 1})
        assert state_file.stat().st_mode & 0o044
