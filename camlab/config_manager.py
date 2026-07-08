"""ConfigManager - owns a delimited managed block in /boot/firmware/config.txt.

The managed block is the single source of truth for the selected sensor overlay
and the rig CSI port:

    # >>> camlab managed (do not edit) >>>
    camera_auto_detect=0
    dtoverlay=ar0822,cam0,4lane
    # <<< camlab managed <<<

Reading is unprivileged (config.txt is world-readable). Writing needs root, so
the GUI shells out to this module's CLI via sudo (see deploy/camlab-sudoers):

    sudo /usr/bin/python3 -m camlab.config_manager set \
        --overlay ar0822 --port cam0 --options 4lane

Port convention (RPi/Kurokesu overlays): base overlay == cam1 (no param),
cam0 is selected by appending ",cam0".
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("CAMLAB_CONFIG_TXT", "/boot/firmware/config.txt"))
OVERLAYS_DIR = Path(os.environ.get("CAMLAB_OVERLAYS_DIR", "/boot/firmware/overlays"))

BEGIN = "# >>> camlab managed (do not edit) >>>"
END = "# <<< camlab managed <<<"

VALID_PORTS = ("cam0", "cam1")

# Privileged shim installed by scripts/setup/config.sh. The only thing the GUI is
# allowed to sudo for the config write (see deploy/camlab-sudoers).
APPLY_BIN = "/usr/local/bin/camlab-apply"


class ConfigError(Exception):
    pass


class ConfigManager:
    def __init__(self, config_path: Path = CONFIG_PATH, overlays_dir: Path = OVERLAYS_DIR):
        self.config_path = Path(config_path)
        self.overlays_dir = Path(overlays_dir)

    # read / inspect (unprivileged)
    def overlay_exists(self, token: str) -> bool:
        return (self.overlays_dir / f"{token}.dtbo").is_file()

    def available_overlays(self) -> list[str]:
        if not self.overlays_dir.is_dir():
            return []
        return sorted(p.stem for p in self.overlays_dir.glob("*.dtbo"))

    def get_current(self) -> dict:
        """Parse the managed block. Returns dict(overlay, port, options, camera_auto_detect, present)."""
        result = {"overlay": None, "port": "cam1", "options": [],
                  "camera_auto_detect": None, "present": False}
        if not self.config_path.is_file():
            return result
        block = self._extract_block(self.config_path.read_text().splitlines())
        if block is None:
            return result
        result["present"] = True
        for line in block:
            line = line.strip()
            if line.startswith("camera_auto_detect="):
                result["camera_auto_detect"] = line.split("=", 1)[1].strip()
            elif line.startswith("dtoverlay="):
                parts = line.split("=", 1)[1].split(",")
                result["overlay"] = parts[0].strip()
                params = [p.strip() for p in parts[1:] if p.strip()]
                ports = [p for p in params if p in VALID_PORTS]
                result["port"] = ports[0] if ports else "cam1"
                result["options"] = [p for p in params if p not in VALID_PORTS]
        return result

    # compose
    @staticmethod
    def compose_dtoverlay(token: str, port: str, options: list[str] | None) -> str:
        if port not in VALID_PORTS:
            raise ConfigError(f"invalid port {port!r} (expected cam0/cam1)")
        parts = [token]
        if port == "cam0":  # cam1 is the overlay default (no param)
            parts.append("cam0")
        parts.extend(o for o in (options or []) if o)
        return "dtoverlay=" + ",".join(parts)

    def _render_block(self, token: str, port: str, options: list[str] | None) -> str:
        return "\n".join([
            BEGIN,
            "camera_auto_detect=0",
            self.compose_dtoverlay(token, port, options),
            END,
        ])

    # write (root)
    def apply(self, token: str, port: str, options: list[str] | None) -> None:
        """Rewrite the managed block. Runs in-process if root, else via sudo helper."""
        if os.geteuid() == 0:
            self._rewrite_in_place(token, port, options)
            return
        if os.path.exists(APPLY_BIN):
            cmd = ["sudo", APPLY_BIN, "set", "--overlay", token, "--port", port]
        else:  # dev fallback when the shim is not installed
            cmd = ["sudo", sys.executable, "-m", "camlab.config_manager",
                   "set", "--overlay", token, "--port", port]
        for o in (options or []):
            cmd += ["--options", o]
        subprocess.run(cmd, check=True)

    def _rewrite_in_place(self, token: str, port: str, options: list[str] | None) -> None:
        if not self.overlay_exists(token):
            raise ConfigError(
                f"overlay '{token}.dtbo' not found in {self.overlays_dir} "
                f"(is the driver installed?)")
        text = self.config_path.read_text() if self.config_path.is_file() else ""
        lines = text.splitlines()
        kept = self._strip_block(lines)
        # Ensure the managed block sits under an [all] context.
        body = "\n".join(kept).rstrip("\n")
        block = self._render_block(token, port, options)
        new_text = (body + "\n\n" if body else "") + block + "\n"
        self._atomic_write(new_text)

    def _atomic_write(self, text: str) -> None:
        tmp = self.config_path.with_suffix(self.config_path.suffix + ".camlab-tmp")
        tmp.write_text(text)
        os.replace(tmp, self.config_path)

    # helpers
    @staticmethod
    def _extract_block(lines: list[str]) -> list[str] | None:
        try:
            i = lines.index(BEGIN)
            j = lines.index(END)
        except ValueError:
            return None
        if j <= i:
            return None
        return lines[i + 1:j]

    @staticmethod
    def _strip_block(lines: list[str]) -> list[str]:
        out, skipping = [], False
        for line in lines:
            if line.strip() == BEGIN:
                skipping = True
                continue
            if line.strip() == END:
                skipping = False
                continue
            if not skipping:
                out.append(line)
        return out


def poweroff() -> None:
    # --no-wall: broadcast would flash on tty1 between Cage exiting and Plymouth
    subprocess.run(["sudo", "systemctl", "poweroff", "--no-wall"], check=True)


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="camlab.config_manager")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_set = sub.add_parser("set", help="rewrite the managed block (root)")
    p_set.add_argument("--overlay", required=True)
    p_set.add_argument("--port", default="cam1", choices=VALID_PORTS)
    p_set.add_argument("--options", action="append", default=[])
    sub.add_parser("get", help="print the current managed block as parsed")
    args = ap.parse_args(argv)

    cm = ConfigManager()
    if args.cmd == "get":
        print(cm.get_current())
        return 0
    if args.cmd == "set":
        if os.geteuid() != 0:
            print("error: 'set' must run as root (sudo)", file=sys.stderr)
            return 2
        cm._rewrite_in_place(args.overlay, args.port, args.options)
        print(f"managed block updated: {cm.compose_dtoverlay(args.overlay, args.port, args.options)}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
