# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Updater - resolves update components to Kurokesu archive packages.

Component ids the GUI asks about, mapped to package names here so the
privileged shim never takes a package name from its caller:

    app             camlab
    driver:<name>   driver_package from data/sensors.yaml
    stack           installed archive packages that are neither of the above

Updates exist only where camlab itself came from the archive, so tarball
installs and forks get no update path (update_path()).

Surveying is unprivileged. Refreshing the archive index needs root via the
sudo shim (deploy/camlab-sudoers):

    sudo /usr/local/bin/camlab-update check

check stamps /var/lib/camlab/update.json, on the data partition so the record
survives the read-only root.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .sensors import SensorRegistry

ARCHIVE_URL = os.environ.get("CAMLAB_ARCHIVE_URL", "https://apt.kurokesu.com")
ARCHIVE_SOURCES = Path(
    os.environ.get("CAMLAB_ARCHIVE_SOURCES", "/etc/apt/sources.list.d/kurokesu.sources")
)
APT_LISTS = Path(os.environ.get("CAMLAB_APT_LISTS", "/var/lib/apt/lists"))

APP_PACKAGE = "camlab"

_STATE_VERSION = 1


class UpdateError(Exception):
    pass


def _run(cmd: list[str]) -> str:
    """Stdout of cmd. Failure raises with the tool's own last line as reason."""
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        lines = (proc.stderr or proc.stdout).strip().splitlines()
        raise UpdateError(lines[-1] if lines else f"{cmd[0]} exited {proc.returncode}")
    return proc.stdout


def _newer(candidate: str, installed: str) -> bool:
    """dpkg version order, so an archive rollback is not offered as an update."""
    proc = subprocess.run(["dpkg", "--compare-versions", candidate, "gt", installed], check=False)
    return proc.returncode == 0


# package inventory
def archive_packages() -> set[str]:
    """Package names the archive serves, read from apt's cached index."""
    prefix = ARCHIVE_URL.split("://", 1)[-1].rstrip("/").replace("/", "_")
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
    """Split apt-cache policy output into {package: {installed, candidate, sites}}.

    sites maps a version to the origins offering it, which tells an archive
    version apart from one dpkg holds alone:

        camlab:
          Installed: 1.0.0
          Candidate: 1.0.1
          Version table:
             1.0.1 500
                500 https://apt.kurokesu.com trixie/main arm64 Packages
         *** 1.0.0 100
                100 /var/lib/dpkg/status
    """
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
    states = {}
    for name, raw in _parse_policy(_run(["apt-cache", "policy", *names])).items():
        installed, candidate = raw["installed"], raw["candidate"]
        sites = raw["sites"].get(installed or "", [])
        pending = candidate if installed and candidate and _newer(candidate, installed) else ""
        states[name] = PackageState(
            name=name,
            installed=installed,
            candidate=candidate,
            from_archive=any(ARCHIVE_URL in site for site in sites),
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
    # Whatever else the archive supplies, so a renamed libcamera soname or a
    # new stack package needs no edit here.
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
    path = path or default_state_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".update-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)  # root writes it, the GUI user reads it
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
        state = {**survey(), "checked": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        write_state(state)
        if state["blocked"]:
            print(f"no update path: {state['blocked']}")
            return 0
        pending = [c["label"] for c in state["components"] if c["pending"]]
        print(f"updates available: {', '.join(pending)}" if pending else "everything up to date")
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
