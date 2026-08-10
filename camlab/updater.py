# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Updater - resolves update components to Kurokesu archive packages.

Component ids map to package names here, so the shim never takes a package
name from its caller:

    app             camlab
    driver:<name>   driver_package from data/sensors.yaml
    stack           installed archive packages that are neither of the above

Only a camlab that came from the archive gets updates, so tarball installs and
forks get none (update_path()).

Surveying is unprivileged. Installing needs root via the sudo shim
(deploy/camlab-sudoers):

    sudo /usr/local/bin/camlab-update check
    sudo /usr/local/bin/camlab-update apply driver:ar0234

apply writes a plan and flips the next boot writable, where
camlab-update.service installs, reapplies setup wiring, relocks and reboots.
Plan and record live on /var/lib/camlab, which survives the read-only root.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .sensors import SensorRegistry

ARCHIVE_URL = os.environ.get("CAMLAB_ARCHIVE_URL", "https://apt.kurokesu.com")
ARCHIVE_SOURCES = Path(
    os.environ.get("CAMLAB_ARCHIVE_SOURCES", "/etc/apt/sources.list.d/kurokesu.sources")
)
APT_LISTS = Path(os.environ.get("CAMLAB_APT_LISTS", "/var/lib/apt/lists"))
MOUNTS = Path(os.environ.get("CAMLAB_MOUNTS", "/proc/self/mounts"))

APP_PACKAGE = "camlab"
UPDATE_BIN = os.environ.get("CAMLAB_UPDATE_BIN", "/usr/local/bin/camlab-update")

FW_DIR = Path(os.environ.get("CAMLAB_FW_DIR", "/boot/firmware"))
CMDLINE = FW_DIR / "cmdline.txt"
OVERLAY_CONF = Path(os.environ.get("CAMLAB_OVERLAY_CONF", "/etc/overlayroot.local.conf"))
# Present boots writable, absent boots read-only. Same token camlabctl rw uses.
WRITABLE = "overlayroot=disabled"

APP_DIR = Path(__file__).resolve().parent.parent
SETUP_DIR = APP_DIR / "scripts" / "setup"
# Version setup last converged for. Root fs, so a reflash resets it.
CONVERGED = Path(os.environ.get("CAMLAB_CONVERGED_FILE", "/var/lib/camlab-setup/converged"))
# Wiring only, so an update boot never rewrites an operator choice or moves a package.
CONVERGE_SCRIPTS = (
    ("journald.sh",),
    ("boot.sh",),
    ("splash.sh",),
    ("shims.sh",),
    ("update.sh",),
    ("service.sh", "--enable"),
)

FBSPLASH = Path(os.environ.get("CAMLAB_FBSPLASH", "/usr/local/lib/camlab/fbsplash.py"))

# A power cut mid-update retries once, then the update gives up.
MAX_ATTEMPTS = 2

# dpkg states that mean an install never finished. Anything else apt can work with.
BROKEN_STATES = frozenset({"half-installed", "unpacked", "half-configured"})

_STATE_VERSION = 1

# About row values standing in for a version.
MAINLINE = "mainline"  # driver and overlay ship with RPi OS
ABSENT = "not installed"


class UpdateError(Exception):
    pass


def _run(cmd: list[str], env: dict[str, str] | None = None) -> str:
    """Stdout of cmd. Failure raises with the tool's own words as reason."""
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    if proc.returncode != 0:
        raise UpdateError(
            _reason(proc.stderr or proc.stdout) or f"{cmd[0]} exited {proc.returncode}"
        )
    return proc.stdout


def _reason(output: str) -> str:
    """First error apt printed. Its last line is the summary of them all."""
    lines = output.strip().splitlines()
    return next((line for line in lines if line.startswith("E:")), lines[-1] if lines else "")


def _run_logged(cmd: list[str], env: dict[str, str] | None = None) -> None:
    """Like _run but output goes to the journal, for the long steps of an update boot."""
    proc = subprocess.run(cmd, check=False, env=env)
    if proc.returncode != 0:
        raise UpdateError(f"{Path(cmd[0]).name} failed (exit {proc.returncode}), see the journal")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _newer(candidate: str, installed: str) -> bool:
    """dpkg version order, so an archive rollback is not offered as an update."""
    proc = subprocess.run(["dpkg", "--compare-versions", candidate, "gt", installed], check=False)
    return proc.returncode == 0


# package inventory
def _archive_key() -> str:
    """Archive URL without its scheme, the form apt uses for index names and policy rows."""
    return ARCHIVE_URL.split("://", 1)[-1].rstrip("/")


def archive_packages() -> set[str]:
    """Package names the archive serves, read from apt's cached index."""
    prefix = _archive_key().replace("/", "_")
    names: set[str] = set()
    for path in APT_LISTS.glob(f"{prefix}*_Packages"):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            # An index that vanished between glob and read is one apt never fetched.
            continue
        for line in text.splitlines():
            if line.startswith("Package: "):
                names.add(line.split(":", 1)[1].strip())
    return names


def installed_packages() -> set[str]:
    """Installed package names. Removed-but-configured names are out."""
    fmt = r"${db:Status-Status} ${Package}\n"
    lines = _run(["dpkg-query", "-Wf", fmt]).splitlines()
    return {parts[1] for parts in (line.split() for line in lines) if parts[:1] == ["installed"]}


def installed_versions(packages: Sequence[str]) -> dict[str, str]:
    """Version per installed package. Names dpkg does not carry are left out."""
    if not packages:
        return {}
    fmt = r"${db:Status-Status} ${Package} ${Version}\n"
    # dpkg-query exits 1 for any name it does not know while still printing the rest.
    proc = subprocess.run(
        ["dpkg-query", "-Wf", fmt, *packages], capture_output=True, text=True, check=False
    )
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == "installed":
            out[parts[1]] = parts[2]
    return out


@dataclass(frozen=True)
class PackageState:
    name: str
    installed: str | None
    candidate: str | None
    from_archive: bool  # installed version is one the archive serves
    pending: str  # candidate worth installing, else empty


def _parse_policy(text: str) -> dict[str, dict]:
    """apt-cache policy output as {package: {installed, candidate, sites per version}}."""
    out: dict[str, dict] = {}
    entry: dict | None = None
    version = ""
    for line in text.splitlines():
        if not line[:1].isspace() and line.rstrip().endswith(":"):
            entry = {"installed": None, "candidate": None, "sites": {}}
            out[line.rstrip()[:-1]] = entry
            version = ""
            continue
        if entry is None:
            continue
        body = line.strip().removeprefix("*** ")
        field, _, value = body.partition(": ")
        if field in ("Installed", "Candidate"):
            entry[field.lower()] = None if value == "(none)" else value
            continue
        parts = body.split()
        if len(parts) < 2:
            continue
        # Version rows read "<version> <pin>", origin rows "<pin> <site>".
        if parts[1].isdigit():
            version = parts[0]
            entry["sites"].setdefault(version, [])
        elif version:
            entry["sites"][version].append(" ".join(parts[1:]))
    return out


def package_states(names: Sequence[str]) -> dict[str, PackageState]:
    """Installed and offered versions for names apt knows about."""
    if not names:
        return {}
    key = _archive_key()
    states = {}
    for name, raw in _parse_policy(_run(["apt-cache", "policy", *names])).items():
        installed, candidate = raw["installed"], raw["candidate"]
        sites = raw["sites"].get(installed or "", [])
        pending = candidate if installed and candidate and _newer(candidate, installed) else ""
        states[name] = PackageState(
            name=name,
            installed=installed,
            candidate=candidate,
            from_archive=any(key in site for site in sites),
            pending=pending,
        )
    return states


# components
@dataclass(frozen=True)
class Component:
    id: str
    label: str
    packages: tuple[str, ...]


def components(registry: SensorRegistry | None = None) -> list[Component]:
    """Updatable components on this box, app first."""
    reg = registry or SensorRegistry.load()
    drivers = {s.overlay: s.driver_package for s in reg if s.driver_package}
    out = [Component("app", APP_PACKAGE, (APP_PACKAGE,))]
    for overlay, package in sorted(drivers.items()):
        out.append(Component(f"driver:{overlay}", f"{overlay} driver", (package,)))
    # Everything else the archive serves, so a libcamera soname bump needs no edit here.
    rest = sorted(
        (archive_packages() & installed_packages()) - {APP_PACKAGE} - set(drivers.values())
    )
    if rest:
        out.append(Component("stack", "camera stack", tuple(rest)))
    return out


def resolve(ident: str, registry: SensorRegistry | None = None) -> Component:
    """Component for an id, rejecting anything the registry does not name."""
    known = components(registry)
    for component in known:
        if component.id == ident:
            return component
    raise UpdateError(f"unknown component {ident!r} (known: {', '.join(c.id for c in known)})")


# gate
def update_path(states: dict[str, PackageState] | None = None) -> str:
    """Empty when updates apply, else the reason they do not."""
    state = (states if states is not None else package_states([APP_PACKAGE])).get(APP_PACKAGE)
    if state is None or state.installed is None:
        return f"{APP_PACKAGE} was not installed as a package"
    if not state.from_archive:
        return f"{APP_PACKAGE} was not installed from {_archive_key()}"
    return ""


def _unreadable_index(error: str) -> bool:
    """apt's wording for a cached index it cannot read, what a power cut leaves."""
    low = error.lower()
    if "could not be parsed" in low or "could not be opened" in low:
        return True
    # "Unable to parse package file X" also fires for dpkg's status file, which
    # dropping an index cannot fix, so it has to name one of ours.
    return "unable to parse" in low and _archive_key().replace("/", "_").lower() in low


def drop_lists() -> int:
    """Delete this archive's cached index. A power cut can leave a file apt cannot parse."""
    prefix = _archive_key().replace("/", "_")
    gone = 0
    for directory in (APT_LISTS, APT_LISTS / "partial"):
        for path in directory.glob(f"{prefix}*"):
            try:
                path.unlink()
                gone += 1
            except OSError:
                pass
    return gone


def refresh() -> None:
    """Refresh the archive index alone, so a slow Debian mirror cannot stall a check."""
    if not ARCHIVE_SOURCES.is_file():
        raise UpdateError(f"{ARCHIVE_SOURCES} missing, archive not enabled on this box")
    _run(
        [
            "apt-get",
            "update",
            # Renaming the archive's suite otherwise wedges refresh until someone
            # clears /var/lib/apt/lists by hand.
            "--allow-releaseinfo-change",
            "-o",
            f"Dir::Etc::sourcelist={ARCHIVE_SOURCES}",
            "-o",
            "Dir::Etc::sourceparts=-",
            "-o",
            "APT::Get::List-Cleanup=0",
        ]
    )


# update boot
def _boot_rw() -> None:
    """The boot partition locks with the root, so lift it before writing cmdline."""
    subprocess.run(["mount", "-o", "remount,rw", str(FW_DIR)], check=False)


def _cmdline_set(token: str, present: bool) -> None:
    """Add or drop one whole cmdline token, leaving the tokens other scripts own."""
    if not CMDLINE.is_file():
        raise UpdateError(f"{CMDLINE} missing")
    _boot_rw()
    tokens = [t for t in CMDLINE.read_text().split() if t != token]
    if present:
        tokens.append(token)
    tmp = CMDLINE.with_suffix(CMDLINE.suffix + ".camlab-tmp")
    try:
        tmp.write_text(" ".join(tokens) + "\n")
        os.replace(tmp, CMDLINE)
    except OSError:
        tmp.unlink(missing_ok=True)
        raise


def unlock_next_boot() -> None:
    """Next boot mounts the real root writable. A dev install already is."""
    if OVERLAY_CONF.is_file():
        _cmdline_set(WRITABLE, True)


def relock() -> None:
    """Drop the writable token. Every exit path out of an update boot calls this."""
    if OVERLAY_CONF.is_file():
        _cmdline_set(WRITABLE, False)


def plan_file() -> Path:
    return default_state_file().parent / "plan.json"


def read_plan() -> dict:
    try:
        with open(plan_file(), "r") as f:
            plan = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(plan, dict) or plan.get("version") != _STATE_VERSION:
        return {}
    return plan


def arm(ids: Sequence[str]) -> list[Component]:
    """Record what to install and flip the next boot writable. A reboot applies it."""
    blocked = update_path()
    if blocked:
        raise UpdateError(blocked)
    chosen = [resolve(i) for i in ids]
    _write_json(
        plan_file(),
        {
            "version": _STATE_VERSION,
            "ids": [c.id for c in chosen],
            "attempts": 0,
            "armed": _now(),
        },
    )
    try:
        unlock_next_boot()
    except Exception:
        # A plan without a writable boot only costs the operator a reboot to learn that.
        disarm()
        raise
    return chosen


def disarm() -> None:
    plan_file().unlink(missing_ok=True)


def _paint(fraction: float, label: str) -> None:
    """Progress bar on every framebuffer. A dark screen must not stop an update."""
    if not FBSPLASH.is_file():
        return
    for fb in sorted(Path("/dev").glob("fb[0-9]*")):
        subprocess.run(
            [
                sys.executable,
                str(FBSPLASH),
                str(fb),
                "--progress",
                f"{fraction:.2f}",
                "--label",
                label,
            ],
            check=False,
        )


class _Progress:
    """Splash bar over a phase of the run, phase-local 0..1 mapped onto the whole bar.

    Repaints only on visible movement, a paint costs a process per framebuffer.
    """

    STEP = 0.02

    def __init__(self) -> None:
        self._span = (0.0, 1.0)
        self._label = ""
        self._painted = -1.0

    def phase(self, start: float, end: float, label: str) -> None:
        self._span = (start, end)
        self._show(start, label)

    def step(self, done: float, label: str | None = None) -> None:
        start, end = self._span
        self._show(start + (end - start) * min(max(done, 0.0), 1.0), label or self._label)

    def finish(self, label: str) -> None:
        self._show(1.0, label)

    def _show(self, fraction: float, label: str) -> None:
        if label == self._label and fraction - self._painted < self.STEP:
            return
        self._label, self._painted = label, fraction
        _paint(fraction, label)


def _root_fstype() -> str:
    try:
        for line in MOUNTS.read_text(errors="replace").splitlines():
            fields = line.split()
            if len(fields) > 2 and fields[1] == "/":
                return fields[2]
    except OSError:
        pass
    return ""


def _require_writable_root() -> None:
    """A locked root writes to a tmpfs upper, so only the mount type gives it away."""
    if _root_fstype() == "overlay":
        raise UpdateError("root is still the overlay, the writable boot did not happen")
    if not os.access("/usr", os.W_OK):
        raise UpdateError("root filesystem is read-only, cannot install")


def _refresh_with_retry(progress: _Progress | None = None, tries: int = 6, delay: int = 10) -> None:
    """Networking comes up alongside this boot, so give the archive a minute."""
    dropped, attempt = False, 0
    while True:
        try:
            refresh()
            return
        except UpdateError as exc:
            attempt += 1
            print(f"refresh attempt {attempt} failed: {exc}", file=sys.stderr)
            # An index apt cannot read fails the same way however long we wait for it.
            if not dropped and (_unreadable_index(str(exc)) or attempt >= tries):
                dropped = True
                if drop_lists():
                    continue
            if attempt >= tries:
                raise
            if progress:
                progress.step(attempt / tries, "Waiting for network")
            time.sleep(delay)


def broken_packages() -> list[str]:
    """Packages dpkg left mid-install, the wreckage a power cut leaves behind."""
    lines = _run(["dpkg-query", "-Wf", r"${db:Status-Status} ${Package}\n"]).splitlines()
    fields = (line.split() for line in lines)
    return [f[1] for f in fields if len(f) > 1 and f[0] in BROKEN_STATES]


def configure_pending(progress: _Progress | None = None) -> None:
    """Finish what dpkg started. Offline, so it heals a box this boot cannot update."""
    if progress and broken_packages():
        progress.step(0.0, "Finishing last update")
    subprocess.run(["dpkg", "--configure", "-a"], check=False)


def repair(progress: _Progress | None = None) -> list[str]:
    """Reinstall what dpkg could not finish, else apt refuses every later update."""
    broken = broken_packages()
    if not broken:
        return []
    if progress:
        progress.step(0.0, "Finishing last update")
    _run_logged(
        ["apt-get", "install", "-y", "--reinstall", *broken],
        env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
    )
    return broken


def _report_apt(line: str, progress: _Progress) -> None:
    """One apt status line, kind:package:percent:description. Fetching takes the first quarter."""
    kind, _, rest = line.partition(":")
    package, _, rest = rest.partition(":")
    try:
        done = float(rest.partition(":")[0]) / 100.0
    except ValueError:
        return
    if kind == "dlstatus":
        progress.step(done * 0.25, "Downloading updates")
    elif kind == "pmstatus":
        driver = package.endswith("-dkms")
        progress.step(
            0.25 + done * 0.75,
            "Rebuilding camera drivers" if driver else "Installing updates",
        )


def _install(packages: Sequence[str], progress: _Progress) -> None:
    """apt on a status pipe, so the splash follows the real work instead of jumping."""
    read_fd, write_fd = os.pipe()
    proc = subprocess.Popen(
        [
            "apt-get",
            "install",
            "-y",
            "--only-upgrade",
            "--no-install-recommends",
            "-o",
            f"APT::Status-Fd={write_fd}",
            *packages,
        ],
        env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
        pass_fds=(write_fd,),
    )
    os.close(write_fd)
    with open(read_fd, encoding="utf-8", errors="replace") as status:
        for line in status:
            _report_apt(line, progress)
    if proc.wait() != 0:
        raise UpdateError(f"apt-get failed (exit {proc.returncode}), see the journal")


def installed_version(package: str) -> str:
    return _run(["dpkg-query", "-W", "-f", "${Version}", package]).strip()


def converge(progress: _Progress | None = None) -> bool:
    """Reapply setup wiring when the app moved past the version it last ran for."""
    version = installed_version(APP_PACKAGE)
    if CONVERGED.is_file() and CONVERGED.read_text().strip() == version:
        return False
    for done, script in enumerate(CONVERGE_SCRIPTS):
        if progress:
            progress.step(done / len(CONVERGE_SCRIPTS))
        _run_logged([str(SETUP_DIR / script[0]), *script[1:]])
    CONVERGED.parent.mkdir(parents=True, exist_ok=True)
    CONVERGED.write_text(f"{version}\n")
    return True


def run() -> str:
    """Body of an update boot. Disarms and relocks on every path, returns the failure."""
    plan = read_plan()
    if not plan:
        return ""
    ids = [str(i) for i in plan.get("ids", [])]
    attempts = int(plan.get("attempts", 0)) + 1
    error = ""
    progress = _Progress()
    if attempts > MAX_ATTEMPTS:
        error = f"update did not finish in {MAX_ATTEMPTS} boots"
    else:
        # Counted before the work, so a power cut counts as an attempt too.
        _write_json(plan_file(), {**plan, "attempts": attempts})
        try:
            progress.phase(0.0, 0.10, "Checking for updates")
            _require_writable_root()
            configure_pending(progress)
            _refresh_with_retry(progress)
            repair(progress)
            progress.phase(0.10, 0.70, "Downloading updates")
            _install(sorted({p for i in ids for p in resolve(i).packages}), progress)
            progress.phase(0.70, 0.95, "Applying settings")
            converge(progress)
        except Exception as exc:  # noqa: BLE001 whatever broke, the box still relocks
            error = str(exc) or type(exc).__name__
    disarm()
    try:
        relock()
    except Exception as exc:  # noqa: BLE001 ExecStopPost retries it, but say so in the record
        error = error or f"relock failed: {exc}"
    _record_run(ids, error)
    _save_log()
    progress.finish("Restarting")
    return error


def _record_run(ids: Sequence[str], error: str) -> None:
    """Leave the outcome where the GUI finds it after the reboot."""
    try:
        state = survey()
    except Exception:  # noqa: BLE001 a failed run is exactly when the survey cannot run
        state = {"version": _STATE_VERSION, "blocked": "", "components": []}
    state["checked"] = _now()
    state["last_run"] = {"finished": _now(), "components": list(ids), "error": error}
    write_state(state)


def _save_log() -> None:
    """The journal of a locked boot lives in RAM, so keep a copy beside the record."""
    path = default_state_file().parent / "update.log"
    try:
        text = _run(["journalctl", "-b", "-u", "camlab-update.service", "--no-pager"])
        path.write_text(text, encoding="utf-8", errors="replace")
        path.chmod(0o644)
    except Exception as exc:  # noqa: BLE001 a missing log must not fail a good update
        print(f"could not copy the journal: {exc}", file=sys.stderr)


# survey and state file
def survey(registry: SensorRegistry | None = None) -> dict:
    """Per-component versions plus the reason updates are off, if they are."""
    comps = components(registry)
    states = package_states(sorted({p for c in comps for p in c.packages}))
    out: dict = {"version": _STATE_VERSION, "blocked": update_path(states), "components": []}
    for component in comps:
        packages = [
            {
                "name": s.name,
                "installed": s.installed,
                "candidate": s.candidate,
                "pending": s.pending,
            }
            for s in (states.get(p) for p in component.packages)
            if s is not None and s.installed
        ]
        if not packages:
            continue
        out["components"].append(
            {
                "id": component.id,
                "label": component.label,
                "pending": any(p["pending"] for p in packages),
                "packages": packages,
            }
        )
    return out


def _row(ident: str, label: str, installed: str, updatable: bool = True) -> dict:
    """One About row. Empty installed means dpkg does not carry the package."""
    return {
        "id": ident,
        "label": label,
        "installed": installed or ABSENT,
        "updatable": updatable and bool(installed),
    }


def _stack_version(packages: Sequence[str], found: dict[str, str]) -> str:
    """One version while a source package ships in step, else how many parts it has."""
    versions = {found[p] for p in packages if p in found}
    return versions.pop() if len(versions) == 1 else f"{len(versions)} packages"


def inventory(registry: SensorRegistry | None = None) -> list[dict]:
    """Every row About shows, from dpkg and uname alone, so it answers offline.

    Not updatable where no press could change the version: a mainline sensor, a
    driver this box does not carry, the kernel that kernel.sh holds.
    """
    reg = registry or SensorRegistry.load()
    stack = next((c for c in components(reg) if c.id == "stack"), None)
    drivers = {s.overlay: s.driver_package for s in reg if s.driver_package}
    found = installed_versions([APP_PACKAGE, *drivers.values(), *(stack.packages if stack else ())])

    rows = [_row("app", APP_PACKAGE, found.get(APP_PACKAGE, ""))]
    # Every sensor, not only packaged ones, or the card leaves half of them unexplained.
    for sensor in sorted(reg, key=lambda s: s.overlay):
        ident, label = f"driver:{sensor.overlay}", f"{sensor.overlay} driver"
        package = drivers.get(sensor.overlay)
        if package is None:
            rows.append(_row(ident, label, MAINLINE, updatable=False))
        else:
            rows.append(_row(ident, label, found.get(package, "")))
    if stack:
        rows.append(_row(stack.id, stack.label, _stack_version(stack.packages, found)))
    # Running kernel, not the held package version, which sits a step ahead until a reboot.
    rows.append(_row("kernel", "kernel", os.uname().release, updatable=False))
    return rows


def _moves_to(installed: str, pending: str) -> str:
    """Pending version, long ones cut back to what differs, ...+krks1-5 to -5."""
    if len(pending) <= 20:
        return pending
    shared = os.path.commonprefix([installed, pending])
    cut = max(shared.rfind(sep) for sep in ".-+~")
    return f"\u2026{pending[cut:]}" if cut > 8 else pending


def component_summary(component: dict) -> tuple[str, str]:
    """(installed, available) row text for one surveyed component.

    Parts share a source and a version, so one move stands for all of them and
    the row never names or counts them.
    """
    packages = component.get("packages") or []
    if not packages:
        return "-", ""
    pending = [p for p in packages if p.get("pending")]
    if not pending:
        if len(packages) == 1:
            return packages[0].get("installed") or "-", ""
        return f"{len(packages)} packages", ""
    lead = pending[0]
    installed = lead.get("installed") or "-"
    return installed, _moves_to(installed, lead["pending"])


def pending_ids(state: dict) -> list[str]:
    """Component ids with something to install, for the GUI to count or offer."""
    return [c["id"] for c in state.get("components") or [] if c.get("pending")]


def check_command() -> list[str]:
    """Refresh and re-survey. One of two verbs sudoers grants the GUI user."""
    return ["sudo", UPDATE_BIN, "check"]


def apply_command(*ids: str) -> list[str]:
    """Arm an update boot for these component ids, or everything pending when none."""
    return ["sudo", UPDATE_BIN, "apply", *ids]


def request_apply(*ids: str) -> None:
    """Arm through the shim and let it reboot. Raises UpdateError with apt's reason."""
    _run(apply_command(*ids))


def default_state_file() -> Path:
    override = os.environ.get("CAMLAB_UPDATE_FILE")
    if override:
        return Path(override)
    state_dir = os.environ.get("STATE_DIRECTORY")
    if state_dir:
        return Path(state_dir.split(":")[0]) / "update.json"
    return Path("/var/lib/camlab/update.json")


def read_state(path: Path | None = None) -> dict:
    """Last recorded check. Missing or corrupt reads as never checked."""
    path = path or default_state_file()
    try:
        with open(path, "r") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("version") != _STATE_VERSION:
        return {}
    return data


def write_state(data: dict, path: Path | None = None) -> None:
    _write_json(path or default_state_file(), data)


def _write_json(path: Path, data: dict) -> None:
    """Atomic and world readable, root writes these and the GUI user reads them."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".camlab-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _require_root(cmd: str) -> bool:
    if os.geteuid() != 0:
        print(f"error: '{cmd}' must run as root (sudo)", file=sys.stderr)
        return False
    return True


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="camlab.updater")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="print components and versions as JSON")
    sub.add_parser("check", help="refresh the archive index and stamp the state file (root)")
    p_show = sub.add_parser("show", help="print the packages a component id resolves to")
    p_show.add_argument("component", help="component id, e.g. app or driver:ar0234")
    p_apply = sub.add_parser("apply", help="arm an update boot and reboot into it (root)")
    p_apply.add_argument("component", nargs="*", help="component ids, default everything pending")
    p_apply.add_argument("--no-reboot", action="store_true", help="arm only, reboot by hand")
    sub.add_parser("run", help="install the armed plan, for the update boot only (root)")
    sub.add_parser("relock", help="drop the writable boot token (root)")
    sub.add_parser("savelog", help="copy this boot's update journal beside the record (root)")
    args = ap.parse_args(argv)

    if args.cmd == "status":
        print(json.dumps({**survey(), "checked": read_state().get("checked", "")}, indent=2))
        return 0
    if args.cmd == "show":
        component = resolve(args.component)
        print(f"{component.id}: {' '.join(component.packages)}")
        return 0
    if args.cmd == "check":
        if not _require_root(args.cmd):
            return 2
        refresh()
        # Merged, so a check does not erase the last update's outcome.
        state = {**read_state(), **survey(), "checked": _now()}
        write_state(state)
        if state["blocked"]:
            print(f"no update path: {state['blocked']}")
            return 0
        pending = [c["label"] for c in state["components"] if c["pending"]]
        print(f"updates available: {', '.join(pending)}" if pending else "everything up to date")
        return 0
    if args.cmd == "apply":
        if not _require_root(args.cmd):
            return 2
        ids = list(args.component) or [c["id"] for c in survey()["components"] if c["pending"]]
        if not ids:
            print("nothing to update")
            return 0
        chosen = arm(ids)
        print(f"armed: {', '.join(c.label for c in chosen)}")
        if args.no_reboot:
            print("reboot to run the update")
            return 0
        subprocess.run(["systemctl", "reboot"], check=False)
        return 0
    if args.cmd == "run":
        if not _require_root(args.cmd):
            return 2
        error = run()
        if error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        return 0
    if args.cmd == "relock":
        if not _require_root(args.cmd):
            return 2
        relock()
        return 0
    if args.cmd == "savelog":
        if not _require_root(args.cmd):
            return 2
        _save_log()
        return 0
    return 1


if __name__ == "__main__":
    try:
        code = _main()
    except UpdateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = 2
    except OSError as exc:
        print(f"error: {exc.strerror or exc}", file=sys.stderr)
        code = 2
    raise SystemExit(code)
