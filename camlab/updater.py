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
    sudo /usr/local/bin/camlab-update apply driver ar0234

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

APP_PACKAGE = "camlab"

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
    ("update.sh",),
    ("service.sh", "--enable"),
)

FBSPLASH = Path(os.environ.get("CAMLAB_FBSPLASH", "/usr/local/lib/camlab/fbsplash.py"))

# A power cut mid-update retries once, then the update gives up.
MAX_ATTEMPTS = 2

_STATE_VERSION = 1


class UpdateError(Exception):
    pass


def _run(cmd: list[str], env: dict[str, str] | None = None) -> str:
    """Stdout of cmd. Failure raises with the tool's own last line as reason."""
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout).strip().splitlines()
        raise UpdateError(lines[-1] if lines else f"{cmd[0]} exited {proc.returncode}")
    return proc.stdout


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
        for line in path.read_text(errors="replace").splitlines():
            if line.startswith("Package: "):
                names.add(line.split(":", 1)[1].strip())
    return names


def installed_packages() -> set[str]:
    """Installed package names. Removed-but-configured names are out."""
    fmt = r"${db:Status-Status} ${Package}\n"
    lines = _run(["dpkg-query", "-Wf", fmt]).splitlines()
    return {parts[1] for parts in (line.split() for line in lines) if parts[:1] == ["installed"]}


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
        return f"{APP_PACKAGE} is not installed as a package"
    if not state.from_archive:
        return f"installed {APP_PACKAGE} {state.installed} did not come from {ARCHIVE_URL}"
    return ""


def refresh() -> None:
    """Refresh the archive index alone, so a slow Debian mirror cannot stall a check."""
    if not ARCHIVE_SOURCES.is_file():
        raise UpdateError(f"{ARCHIVE_SOURCES} missing, archive not enabled on this box")
    _run(
        [
            "apt-get",
            "update",
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
    unlock_next_boot()
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


def _require_writable_root() -> None:
    """Fail here rather than deep inside dpkg if the boot came up read-only anyway."""
    if not os.access("/usr", os.W_OK):
        raise UpdateError("root filesystem is read-only, cannot install")


def _refresh_with_retry(progress: _Progress | None = None, tries: int = 6, delay: int = 10) -> None:
    """Networking comes up alongside this boot, so give the archive a minute."""
    for left in range(tries - 1, -1, -1):
        try:
            refresh()
            return
        except UpdateError:
            if left == 0:
                raise
            if progress:
                progress.step(1.0 - left / tries, "Waiting for network")
            time.sleep(delay)


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
            _refresh_with_retry(progress)
            progress.phase(0.10, 0.70, "Downloading updates")
            _install(sorted({p for i in ids for p in resolve(i).packages}), progress)
            progress.phase(0.70, 0.95, "Applying settings")
            converge(progress)
        except UpdateError as exc:
            error = str(exc)
    disarm()
    relock()
    _record_run(ids, error)
    progress.finish("Restarting")
    return error


def _record_run(ids: Sequence[str], error: str) -> None:
    """Leave the outcome where the GUI finds it after the reboot."""
    try:
        state = survey()
    except UpdateError:
        state = {"version": _STATE_VERSION, "blocked": "", "components": []}
    state["checked"] = _now()
    state["last_run"] = {"finished": _now(), "components": list(ids), "error": error}
    write_state(state)


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
    p_show.add_argument("component", nargs="+", help="component id, e.g. app or driver ar0234")
    p_apply = sub.add_parser("apply", help="arm an update boot and reboot into it (root)")
    p_apply.add_argument("component", nargs="*", help="component id, default everything pending")
    p_apply.add_argument("--no-reboot", action="store_true", help="arm only, reboot by hand")
    sub.add_parser("run", help="install the armed plan, for the update boot only (root)")
    sub.add_parser("relock", help="drop the writable boot token (root)")
    args = ap.parse_args(argv)

    if args.cmd == "status":
        print(json.dumps({**survey(), "checked": read_state().get("checked", "")}, indent=2))
        return 0
    if args.cmd == "show":
        component = resolve(":".join(args.component))
        print(f"{component.id}: {' '.join(component.packages)}")
        return 0
    if args.cmd == "check":
        if not _require_root(args.cmd):
            return 2
        refresh()
        state = {**survey(), "checked": _now()}
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
        if args.component:
            ids = [":".join(args.component)]
        else:
            ids = [c["id"] for c in survey()["components"] if c["pending"]]
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
