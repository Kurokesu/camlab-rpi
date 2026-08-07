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


def fake_resolve(ident: str, registry: SensorRegistry | None = None) -> Component:
    """Stand in for registry lookups, one package named after the component."""
    return Component(ident, ident, (f"{ident}-pkg",))


def raiser(message: str, kind: type[Exception] = UpdateError):
    def fail(*args, **kwargs):
        raise kind(message)

    return fail


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


class TestReason:
    """A failure is recorded as one line, so it should be the one naming the problem."""

    def test_the_first_error_wins_over_apt_summary(self):
        text = (
            "Err:1 https://apt.kurokesu.com trixie InRelease\n"
            "  Could not connect to apt.kurokesu.com\n"
            "E: Failed to fetch https://apt.kurokesu.com/dists/trixie/InRelease\n"
            "E: Some index files failed to download.\n"
        )
        assert updater._reason(text).startswith("E: Failed to fetch")

    def test_without_an_error_line_the_last_one_stands(self):
        assert updater._reason("dpkg: warning\nsomething went wrong\n") == "something went wrong"

    def test_no_output_reads_as_empty(self):
        assert updater._reason("") == ""


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

    def test_staging_archive_on_disk_counts(self, policy, monkeypatch):
        """apt prints file: URLs with one slash, validation runs against such an archive."""
        monkeypatch.setattr(updater, "ARCHIVE_URL", "file:///srv/camlab-staging")
        policy(FROM_ARCHIVE.replace("https://apt.kurokesu.com", "file:/srv/camlab-staging"))
        assert updater.package_states(["camlab"])["camlab"].from_archive

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

    def test_staging_archive_path_becomes_the_index_prefix(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(updater, "APT_LISTS", tmp_path)
        monkeypatch.setattr(updater, "ARCHIVE_URL", "file:///srv/camlab-staging")
        (tmp_path / "_srv_camlab-staging_dists_trixie_main_binary-arm64_Packages").write_text(
            "Package: camlab\nVersion: 1.0.0\n"
        )
        assert updater.archive_packages() == {"camlab"}

    def test_unreadable_index_is_skipped(self, tmp_path: Path, monkeypatch):
        """An index apt never fetched must not take the whole survey down with it."""
        monkeypatch.setattr(updater, "APT_LISTS", tmp_path)
        (tmp_path / "apt.kurokesu.com_dists_trixie_main_binary-arm64_Packages").mkdir()
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


class TestCmdline:
    @pytest.fixture(autouse=True)
    def readonly_box(self, tmp_path: Path, monkeypatch) -> Path:
        cmdline = tmp_path / "cmdline.txt"
        cmdline.write_text("console=serial0,115200 root=PARTUUID=abc rootwait quiet\n")
        monkeypatch.setattr(updater, "CMDLINE", cmdline)
        monkeypatch.setattr(updater, "_boot_rw", lambda: None)
        monkeypatch.setattr(updater, "OVERLAY_CONF", tmp_path / "overlayroot.local.conf")
        (tmp_path / "overlayroot.local.conf").touch()
        return cmdline

    def test_unlock_appends_the_token(self, readonly_box: Path):
        updater.unlock_next_boot()
        assert readonly_box.read_text().split()[-1] == updater.WRITABLE

    def test_unlock_twice_leaves_one_token(self, readonly_box: Path):
        updater.unlock_next_boot()
        updater.unlock_next_boot()
        assert readonly_box.read_text().count(updater.WRITABLE) == 1

    def test_relock_drops_it_and_keeps_the_rest(self, readonly_box: Path):
        updater.unlock_next_boot()
        updater.relock()
        assert readonly_box.read_text().split() == [
            "console=serial0,115200",
            "root=PARTUUID=abc",
            "rootwait",
            "quiet",
        ]

    def test_relock_without_the_token_is_a_no_op(self, readonly_box: Path):
        before = readonly_box.read_text().split()
        updater.relock()
        assert readonly_box.read_text().split() == before

    def test_writable_box_keeps_cmdline_untouched(self, readonly_box: Path, monkeypatch):
        """No overlay config means the root is already writable, nothing to flip."""
        monkeypatch.setattr(updater, "OVERLAY_CONF", readonly_box.parent / "absent")
        updater.unlock_next_boot()
        assert updater.WRITABLE not in readonly_box.read_text()


class TestArm:
    @pytest.fixture(autouse=True)
    def armable(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CAMLAB_UPDATE_FILE", str(tmp_path / "update.json"))
        monkeypatch.setattr(updater, "OVERLAY_CONF", tmp_path / "absent")
        monkeypatch.setattr(updater, "update_path", lambda states=None: "")
        monkeypatch.setattr(updater, "resolve", fake_resolve)

    def test_plan_records_the_ids(self):
        updater.arm(["app", "driver:ar0234"])
        assert updater.read_plan()["ids"] == ["app", "driver:ar0234"]

    def test_arm_refuses_without_an_update_path(self, monkeypatch):
        monkeypatch.setattr(updater, "update_path", lambda states=None: "tarball install")
        with pytest.raises(UpdateError, match="tarball install"):
            updater.arm(["app"])

    def test_disarm_clears_the_plan(self):
        updater.arm(["app"])
        updater.disarm()
        assert updater.read_plan() == {}

    def test_a_plan_without_a_writable_boot_is_dropped(self, monkeypatch):
        """Otherwise the next boot runs an update it cannot install and says so."""
        monkeypatch.setattr(updater, "unlock_next_boot", raiser("cmdline.txt missing"))
        with pytest.raises(UpdateError):
            updater.arm(["app"])
        assert updater.read_plan() == {}


class TestRun:
    @pytest.fixture(autouse=True)
    def update_boot(self, tmp_path: Path, monkeypatch):
        """An armed box with apt, converge and the framebuffer stubbed out."""
        monkeypatch.setenv("CAMLAB_UPDATE_FILE", str(tmp_path / "update.json"))
        monkeypatch.setattr(updater, "OVERLAY_CONF", tmp_path / "absent")
        monkeypatch.setattr(updater, "FBSPLASH", tmp_path / "absent")
        monkeypatch.setattr(updater, "resolve", fake_resolve)
        monkeypatch.setattr(updater.os, "access", lambda path, mode: True)
        mounts = tmp_path / "mounts"
        mounts.write_text("/dev/mmcblk0p2 / ext4 rw,relatime 0 0\n")
        monkeypatch.setattr(updater, "MOUNTS", mounts)
        monkeypatch.setattr(updater, "_save_log", lambda: None)
        monkeypatch.setattr(updater, "configure_pending", lambda: None)
        monkeypatch.setattr(updater, "repair", lambda progress=None: [])
        monkeypatch.setattr(updater, "_refresh_with_retry", lambda progress=None: None)
        monkeypatch.setattr(updater, "converge", lambda progress=None: True)
        monkeypatch.setattr(updater, "survey", lambda reg=None: {"version": 1, "components": []})
        self.installed = []
        monkeypatch.setattr(
            updater, "_install", lambda packages, progress=None: self.installed.append(packages)
        )

    def arm(self, attempts: int = 0) -> None:
        plan = {"version": 1, "ids": ["app"], "attempts": attempts, "armed": "now"}
        updater.write_state(plan, updater.plan_file())

    def test_installs_what_the_plan_names(self):
        self.arm()
        assert updater.run() == ""
        assert self.installed == [["app-pkg"]]

    def test_success_disarms(self):
        self.arm()
        updater.run()
        assert updater.read_plan() == {}

    def test_failure_disarms_and_is_recorded(self, monkeypatch):
        self.arm()
        monkeypatch.setattr(updater, "_refresh_with_retry", raiser("no dns"))
        assert updater.run() == "no dns"
        assert updater.read_plan() == {}
        assert updater.read_state()["last_run"]["error"] == "no dns"

    def test_last_attempt_gives_up_without_installing(self):
        """A power cut counted an attempt, so this boot stops instead of retrying forever."""
        self.arm(attempts=updater.MAX_ATTEMPTS)
        assert "did not finish" in updater.run()
        assert self.installed == []

    def test_attempt_is_counted_before_the_work(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            updater,
            "_install",
            lambda packages, progress=None: seen.append(updater.read_plan()["attempts"]),
        )
        self.arm()
        updater.run()
        assert seen == [1]

    def test_unarmed_boot_does_nothing(self):
        assert updater.run() == ""
        assert self.installed == []

    def test_read_only_root_stops_before_apt(self, monkeypatch):
        """The cmdline flip did not take, so this boot cannot install anything."""
        self.arm()
        monkeypatch.setattr(updater.os, "access", lambda path, mode: False)
        assert "read-only" in updater.run()
        assert self.installed == []

    def test_overlay_root_stops_before_apt(self, tmp_path: Path):
        """A locked root is writable through a tmpfs upper, so only its type gives it away."""
        self.arm()
        (tmp_path / "mounts").write_text("overlayroot / overlay rw,relatime,lowerdir=/ 0 0\n")
        assert "overlay" in updater.run()
        assert self.installed == []

    def test_any_failure_is_recorded(self, monkeypatch):
        """Not just UpdateError. A bare crash told the operator the update had worked."""
        self.arm()
        monkeypatch.setattr(updater, "converge", raiser("boom", ValueError))
        assert updater.run() == "boom"
        assert updater.read_plan() == {}
        assert updater.read_state()["last_run"]["error"] == "boom"

    def test_failure_without_a_message_still_names_itself(self, monkeypatch):
        self.arm()
        monkeypatch.setattr(updater, "converge", raiser("", FileNotFoundError))
        assert updater.run() == "FileNotFoundError"

    def test_relock_failure_reaches_the_record(self, monkeypatch):
        """A box left writable is the one failure the reboot cannot fix by itself."""
        self.arm()
        monkeypatch.setattr(updater, "relock", raiser("cmdline.txt missing"))
        assert "cmdline.txt missing" in updater.run()
        assert "cmdline.txt missing" in updater.read_state()["last_run"]["error"]

    def test_the_last_install_is_finished_before_this_one(self, monkeypatch):
        """A power cut leaves dpkg mid-install, and apt then refuses every later update."""
        order = []
        monkeypatch.setattr(updater, "configure_pending", lambda: order.append("dpkg"))
        monkeypatch.setattr(updater, "repair", lambda progress=None: order.append("repair"))
        monkeypatch.setattr(
            updater, "_install", lambda packages, progress=None: order.append("install")
        )
        self.arm()
        updater.run()
        assert order == ["dpkg", "repair", "install"]


class TestRepair:
    """What a power cut during an install leaves behind."""

    @pytest.fixture(autouse=True)
    def dpkg(self, monkeypatch):
        self.ran: list[list[str]] = []
        monkeypatch.setattr(updater.subprocess, "run", lambda cmd, check: self.ran.append(cmd))
        monkeypatch.setattr(updater, "_run_logged", lambda cmd, env=None: self.ran.append(cmd[:4]))

    def feed(self, monkeypatch, text: str) -> None:
        monkeypatch.setattr(updater, "_run", lambda cmd, env=None: text)

    def test_half_installed_packages_are_named(self, monkeypatch):
        self.feed(
            monkeypatch,
            "installed camlab\nhalf-installed ar0822-rpi-dkms\nconfig-files old-thing\n"
            "unpacked imx585-rpi-dkms\nnot-installed never-here\n",
        )
        assert updater.broken_packages() == ["ar0822-rpi-dkms", "imx585-rpi-dkms"]

    def test_reinstall_covers_them(self, monkeypatch):
        self.feed(monkeypatch, "half-configured ar0822-rpi-dkms\n")
        assert updater.repair() == ["ar0822-rpi-dkms"]
        assert self.ran == [["apt-get", "install", "-y", "--reinstall"]]

    def test_a_clean_box_reinstalls_nothing(self, monkeypatch):
        self.feed(monkeypatch, "installed camlab\n")
        assert updater.repair() == []
        assert self.ran == []

    def test_pending_configure_runs_offline(self):
        """Before the refresh, so it heals a box that cannot reach the archive either."""
        updater.configure_pending()
        assert self.ran == [["dpkg", "--configure", "-a"]]


class TestLogCopy:
    """A boot that comes up locked keeps its journal in RAM, so the record needs a copy."""

    @pytest.fixture(autouse=True)
    def data_mount(self, tmp_path: Path, monkeypatch) -> Path:
        monkeypatch.setenv("CAMLAB_UPDATE_FILE", str(tmp_path / "update.json"))
        return tmp_path

    def test_journal_lands_beside_the_record(self, data_mount: Path, monkeypatch):
        monkeypatch.setattr(updater, "_run", lambda cmd, env=None: "journal text\n")
        updater._save_log()
        assert (data_mount / "update.log").read_text() == "journal text\n"

    def test_missing_journal_is_not_a_failure(self, data_mount: Path, monkeypatch):
        monkeypatch.setattr(updater, "_run", raiser("no journal"))
        updater._save_log()
        assert not (data_mount / "update.log").exists()


class TestSplashProgress:
    @pytest.fixture(autouse=True)
    def painter(self, monkeypatch):
        self.paints: list[tuple[float, str]] = []
        monkeypatch.setattr(
            updater,
            "_paint",
            lambda fraction, label: self.paints.append((round(fraction, 3), label)),
        )

    def test_phase_maps_onto_its_slice(self):
        progress = updater._Progress()
        progress.phase(0.10, 0.70, "Downloading updates")
        progress.step(0.5)
        assert self.paints == [(0.10, "Downloading updates"), (0.40, "Downloading updates")]

    def test_small_movement_does_not_repaint(self):
        """Every paint spawns a process per framebuffer, so apt chatter must not drive it."""
        progress = updater._Progress()
        progress.phase(0.0, 1.0, "Installing updates")
        progress.step(0.001)
        assert self.paints == [(0.0, "Installing updates")]

    def test_new_label_always_repaints(self):
        progress = updater._Progress()
        progress.phase(0.0, 1.0, "Installing updates")
        progress.step(0.001, "Rebuilding camera drivers")
        assert [label for _, label in self.paints] == [
            "Installing updates",
            "Rebuilding camera drivers",
        ]

    def test_fetch_and_install_split_the_phase(self):
        progress = updater._Progress()
        progress.phase(0.0, 1.0, "start")
        updater._report_apt("dlstatus:camlab:100:Retrieved\n", progress)
        updater._report_apt("pmstatus:camlab:0:Unpacking\n", progress)
        assert self.paints[1:] == [(0.25, "Downloading updates"), (0.25, "Installing updates")]

    def test_driver_configure_names_the_rebuild(self):
        progress = updater._Progress()
        progress.phase(0.0, 1.0, "start")
        updater._report_apt("pmstatus:ar0822-rpi-dkms:40:Setting up\n", progress)
        assert self.paints[-1] == (0.55, "Rebuilding camera drivers")

    def test_line_without_a_percentage_is_ignored(self):
        updater._report_apt("nonsense\n", updater._Progress())
        assert self.paints == []


class TestRefreshRetry:
    def test_keeps_trying_while_the_network_settles(self, monkeypatch):
        calls = []

        def flaky() -> None:
            calls.append(1)
            if len(calls) < 3:
                raise UpdateError("temporary failure resolving")

        monkeypatch.setattr(updater, "refresh", flaky)
        updater._refresh_with_retry(tries=5, delay=0)
        assert len(calls) == 3

    def test_gives_up_with_apt_own_reason(self, monkeypatch):
        monkeypatch.setattr(updater, "refresh", raiser("could not resolve host"))
        monkeypatch.setattr(updater, "drop_lists", lambda: 0)
        with pytest.raises(UpdateError, match="could not resolve host"):
            updater._refresh_with_retry(tries=2, delay=0)

    def test_an_unparsable_index_is_dropped_and_fetched_again(self, monkeypatch):
        """A power cut can truncate a cached Release, and apt then fails the same way forever."""
        calls = []
        monkeypatch.setattr(updater, "drop_lists", lambda: 1)

        def flaky() -> None:
            calls.append(1)
            if len(calls) < 3:
                raise UpdateError("The package lists could not be parsed")

        monkeypatch.setattr(updater, "refresh", flaky)
        updater._refresh_with_retry(tries=2, delay=0)
        assert len(calls) == 3


class TestDropLists:
    def test_only_this_archive_loses_its_index(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(updater, "APT_LISTS", tmp_path)
        (tmp_path / "partial").mkdir()
        for name in (
            "apt.kurokesu.com_dists_trixie_InRelease",
            "partial/apt.kurokesu.com_dists_trixie_Release",
            "deb.debian.org_dists_trixie_InRelease",
        ):
            (tmp_path / name).write_text("")
        assert updater.drop_lists() == 2
        assert [p.name for p in tmp_path.glob("*_InRelease")] == [
            "deb.debian.org_dists_trixie_InRelease"
        ]

    def test_nothing_cached_drops_nothing(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(updater, "APT_LISTS", tmp_path / "gone")
        assert updater.drop_lists() == 0


class TestConverge:
    @pytest.fixture(autouse=True)
    def setup_tree(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(updater, "CONVERGED", tmp_path / "converged")
        monkeypatch.setattr(updater, "SETUP_DIR", tmp_path / "setup")
        monkeypatch.setattr(updater, "installed_version", lambda package: "1.0.1")
        self.ran: list[str] = []
        monkeypatch.setattr(updater, "_run_logged", lambda cmd, env=None: self.ran.append(cmd[0]))

    def test_runs_the_wiring_scripts_when_the_version_moved(self, tmp_path: Path):
        (tmp_path / "converged").write_text("1.0.0\n")
        assert updater.converge()
        assert [Path(c).name for c in self.ran] == [s[0] for s in updater.CONVERGE_SCRIPTS]

    def test_first_update_converges(self):
        assert updater.converge()

    def test_same_version_skips(self, tmp_path: Path):
        (tmp_path / "converged").write_text("1.0.1\n")
        assert not updater.converge()
        assert self.ran == []

    def test_marker_records_the_new_version(self, tmp_path: Path):
        updater.converge()
        assert (tmp_path / "converged").read_text().strip() == "1.0.1"

    def test_operator_owned_scripts_stay_out(self):
        """config.sh writes the sensor block, drivers/deps install packages."""
        names = [s[0] for s in updater.CONVERGE_SCRIPTS]
        assert not {"config.sh", "display.sh", "deps.sh", "drivers.sh", "readonly.sh"} & set(names)


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


class TestCli:
    @pytest.fixture(autouse=True)
    def rooted(self, monkeypatch):
        # os.geteuid is absent on the dev machines these tests also run on.
        monkeypatch.setattr(updater, "_require_root", lambda cmd: True)
        monkeypatch.setattr(updater, "resolve", fake_resolve)
        self.armed: list[list[str]] = []
        monkeypatch.setattr(updater, "arm", lambda ids: self.armed.append(list(ids)) or [])

    def test_apply_takes_several_ids(self):
        """Ids used to be joined with a colon, which made two ids one unknown component."""
        assert updater._main(["apply", "app", "driver:ar0234", "--no-reboot"]) == 0
        assert self.armed == [["app", "driver:ar0234"]]

    def test_bare_apply_takes_everything_pending(self, monkeypatch):
        monkeypatch.setattr(
            updater,
            "survey",
            lambda: {"components": [{"id": "app", "pending": True}, {"id": "stack", "pending": 0}]},
        )
        updater._main(["apply", "--no-reboot"])
        assert self.armed == [["app"]]

    def test_show_takes_one_id(self, capsys):
        updater._main(["show", "driver:ar0234"])
        assert capsys.readouterr().out.startswith("driver:ar0234: driver:ar0234-pkg")

    def test_check_keeps_the_last_update_outcome(self, tmp_path: Path, monkeypatch):
        """The GUI checks on its own, and used to wipe the record of the update it ran."""
        monkeypatch.setenv("CAMLAB_UPDATE_FILE", str(tmp_path / "update.json"))
        monkeypatch.setattr(updater, "refresh", lambda: None)
        monkeypatch.setattr(updater, "survey", lambda: {"blocked": "", "components": []})
        updater.write_state({"version": 1, "last_run": {"error": "no dns"}})
        updater._main(["check"])
        assert updater.read_state()["last_run"] == {"error": "no dns"}
