# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""ConfigManager - owns camlab managed blocks in /boot/firmware/config.txt.

Camera block selects sensor overlay and rig CSI port. Display block owns DSI
touch panel overlay:

    # >>> camlab managed (do not edit) >>>
    camera_auto_detect=0
    dtoverlay=ar0822,cam0,4lane
    # <<< camlab managed <<<

    # >>> camlab display (do not edit) >>>
    display_auto_detect=0
    dtoverlay=vc4-kms-dsi-7inch
    # <<< camlab display <<<

Reading is unprivileged. Writes need root via sudo CLI (deploy/camlab-sudoers):

    sudo /usr/bin/python3 -m camlab.config_manager set \
        --overlay ar0822 --port cam0 --options 4lane

Camera overlays default to cam1, append ",cam0" for cam0. DSI overlays default
to DISP1, ",dsi0" selects DISP0.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from .drm import dsi_blocked_ports

CONFIG_PATH = Path(os.environ.get("CAMLAB_CONFIG_TXT", "/boot/firmware/config.txt"))
OVERLAYS_DIR = Path(os.environ.get("CAMLAB_OVERLAYS_DIR", "/boot/firmware/overlays"))
MODEL_PATH = Path(os.environ.get("CAMLAB_DT_MODEL", "/proc/device-tree/model"))

BEGIN = "# >>> camlab managed (do not edit) >>>"
END = "# <<< camlab managed <<<"
DISPLAY_BEGIN = "# >>> camlab display (do not edit) >>>"
DISPLAY_END = "# <<< camlab display <<<"

VALID_PORTS = ("cam0", "cam1")


def is_compute_module() -> bool:
    try:
        return "Compute Module" in MODEL_PATH.read_text()
    except OSError:
        return False


# Privileged shim from scripts/setup/config.sh. Only config write GUI may sudo.
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
        """Parse camera block into dict(overlay, port, options, camera_auto_detect, present)."""
        result = {
            "overlay": None,
            "port": "cam1",
            "options": [],
            "camera_auto_detect": None,
            "present": False,
        }
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

    def get_current_display(self) -> dict:
        """Parse display block into dict(overlay, dsi0, port_blocked, present).
        overlay is raw dtoverlay value, port_blocked is CSI port panel claims next boot."""
        result = {"overlay": None, "dsi0": False, "port_blocked": None, "present": False}
        if not self.config_path.is_file():
            return result
        lines = self.config_path.read_text().splitlines()
        block = self._extract_block(lines, DISPLAY_BEGIN, DISPLAY_END)
        if block is None:
            return result
        result["present"] = True
        for line in block:
            line = line.strip()
            if line.startswith("dtoverlay="):
                raw = line.split("=", 1)[1].strip()
                result["overlay"] = raw
                result["dsi0"] = "dsi0" in raw.split(",")[1:]
                result["port_blocked"] = "cam0" if result["dsi0"] else "cam1"
        return result

    def blocked_ports_next_boot(self) -> set[str]:
        """CSI ports display claims next boot. Live DRM lags pending changes.
        Display block when present, else live DRM on Pi 5, nothing on CM."""
        disp = self.get_current_display()
        if disp["present"]:
            return {disp["port_blocked"]} if disp["port_blocked"] else set()
        if is_compute_module():
            return set()
        return dsi_blocked_ports()

    def free_port(self) -> str:
        """CSI port the display leaves alone, cam1 first as overlay default."""
        blocked = self.blocked_ports_next_boot()
        for port in ("cam1", "cam0"):
            if port not in blocked:
                return port
        raise ConfigError("no free CSI port, display claims both connectors")

    def _require_free_port(self, port: str) -> None:
        if port in self.blocked_ports_next_boot():
            raise ConfigError(
                f"{port} is claimed by the display overlay (shared CSI/DSI connector)"
            )

    # compose
    @staticmethod
    def compose_dtoverlay(token: str, port: str, options: list[str] | None) -> str:
        if port not in VALID_PORTS:
            raise ConfigError(f"invalid port {port!r} (expected cam0/cam1)")
        parts = [token]
        if port == "cam0":  # cam1 is overlay default (no param)
            parts.append("cam0")
        parts.extend(o for o in (options or []) if o)
        return "dtoverlay=" + ",".join(parts)

    @staticmethod
    def compose_display_overlay(token: str, cam_port: str) -> str:
        """Panel on connector camera does not use: cam0 --> DISP1, cam1 --> DISP0."""
        if cam_port not in VALID_PORTS:
            raise ConfigError(f"invalid port {cam_port!r} (expected cam0/cam1)")
        return token if cam_port == "cam0" else f"{token},dsi0"

    def _render_block(self, token: str, port: str, options: list[str] | None) -> str:
        return "\n".join(
            [
                BEGIN,
                "camera_auto_detect=0",
                self.compose_dtoverlay(token, port, options),
                END,
            ]
        )

    # write (root)
    def apply(self, token: str, port: str, options: list[str] | None) -> None:
        """Rewrite camera block. In-process as root, else via sudo helper."""
        # Fail before spawning sudo. Privileged path re-checks.
        self._require_free_port(port)
        if os.geteuid() == 0:
            self._rewrite_in_place(token, port, options)
            return
        if os.path.exists(APPLY_BIN):
            cmd = ["sudo", APPLY_BIN, "set", "--overlay", token, "--port", port]
        else:  # dev fallback when shim not installed
            cmd = [
                "sudo",
                sys.executable,
                "-m",
                "camlab.config_manager",
                "set",
                "--overlay",
                token,
                "--port",
                port,
            ]
        for o in options or []:
            cmd += ["--options", o]
        subprocess.run(cmd, check=True)

    def _rewrite_in_place(self, token: str, port: str, options: list[str] | None) -> None:
        if not self.overlay_exists(token):
            raise ConfigError(
                f"overlay '{token}.dtbo' not found in {self.overlays_dir} "
                f"(is the driver installed?)"
            )
        self._require_free_port(port)
        text = self.config_path.read_text() if self.config_path.is_file() else ""
        lines = text.splitlines()
        kept = self._strip_block(lines)
        # Append last so block sits under [all] context.
        body = "\n".join(kept).rstrip("\n")
        block = self._render_block(token, port, options)
        new_text = (body + "\n\n" if body else "") + block + "\n"
        self._atomic_write(new_text)

    def apply_display(self, raw_overlay: str | None) -> None:
        """Rewrite display block, None removes it. In-process as root, else via sudo helper."""
        if os.geteuid() == 0:
            self._rewrite_display_in_place(raw_overlay)
            return
        if raw_overlay is None:
            args = ["display-clear"]
        else:
            args = ["display-set", "--overlay", raw_overlay]
        if os.path.exists(APPLY_BIN):
            cmd = ["sudo", APPLY_BIN, *args]
        else:  # dev fallback when shim not installed
            cmd = ["sudo", sys.executable, "-m", "camlab.config_manager", *args]
        subprocess.run(cmd, check=True)

    def _rewrite_display_in_place(self, raw_overlay: str | None) -> None:
        if raw_overlay is not None:
            token = raw_overlay.split(",")[0]
            if not self.overlay_exists(token):
                raise ConfigError(
                    f"overlay '{token}.dtbo' not found in {self.overlays_dir} "
                    f"(is the OS image complete?)"
                )
        text = self.config_path.read_text() if self.config_path.is_file() else ""
        kept = self._strip_block(text.splitlines(), DISPLAY_BEGIN, DISPLAY_END)
        body = "\n".join(kept).rstrip("\n")
        new_text = body + "\n" if body else ""
        if raw_overlay is not None:
            block = "\n".join(
                [
                    DISPLAY_BEGIN,
                    # Explicit overlay owns panel. Stop Pi 5 firmware loading it twice.
                    "display_auto_detect=0",
                    f"dtoverlay={raw_overlay}",
                    DISPLAY_END,
                ]
            )
            new_text = (body + "\n\n" if body else "") + block + "\n"
        self._atomic_write(new_text)

    def _atomic_write(self, text: str) -> None:
        tmp = self.config_path.with_suffix(self.config_path.suffix + ".camlab-tmp")
        tmp.write_text(text)
        os.replace(tmp, self.config_path)

    # helpers
    @staticmethod
    def _extract_block(lines: list[str], begin: str = BEGIN, end: str = END) -> list[str] | None:
        try:
            i = lines.index(begin)
            j = lines.index(end)
        except ValueError:
            return None
        if j <= i:
            return None
        return lines[i + 1 : j]

    @staticmethod
    def _strip_block(lines: list[str], begin: str = BEGIN, end: str = END) -> list[str]:
        out, skipping = [], False
        for line in lines:
            if line.strip() == begin:
                skipping = True
                continue
            if line.strip() == end:
                skipping = False
                continue
            if not skipping:
                out.append(line)
        return out


def poweroff() -> None:
    # --no-wall: broadcast would flash tty1 between Cage exit and Plymouth
    subprocess.run(["sudo", "systemctl", "poweroff", "--no-wall"], check=True)


def _require_root(cmd: str) -> bool:
    if os.geteuid() != 0:
        print(f"error: '{cmd}' must run as root (sudo)", file=sys.stderr)
        return False
    return True


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="camlab.config_manager")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_set = sub.add_parser("set", help="rewrite the camera block (root)")
    p_set.add_argument("--overlay", required=True)
    p_set.add_argument("--port", default="cam1", choices=VALID_PORTS)
    p_set.add_argument("--options", action="append", default=[])
    sub.add_parser("get", help="print the current camera block as parsed")
    sub.add_parser("free-port", help="print a CSI port the display does not claim")
    p_disp = sub.add_parser("display-set", help="write the display block (root)")
    p_disp.add_argument("--overlay", required=True, help="raw overlay, params allowed (token,dsi0)")
    sub.add_parser("display-clear", help="remove the display block (root)")
    sub.add_parser("display-get", help="print the current display block as parsed")
    args = ap.parse_args(argv)

    cm = ConfigManager()
    if args.cmd == "get":
        print(cm.get_current())
        return 0
    if args.cmd == "display-get":
        print(cm.get_current_display())
        return 0
    if args.cmd == "free-port":
        print(cm.free_port())
        return 0
    if args.cmd == "set":
        if not _require_root(args.cmd):
            return 2
        cm._rewrite_in_place(args.overlay, args.port, args.options)
        print(
            f"camera block updated: {cm.compose_dtoverlay(args.overlay, args.port, args.options)}"
        )
        return 0
    if args.cmd == "display-set":
        if not _require_root(args.cmd):
            return 2
        cm._rewrite_display_in_place(args.overlay)
        print(f"display block updated: dtoverlay={args.overlay}")
        return 0
    if args.cmd == "display-clear":
        if not _require_root(args.cmd):
            return 2
        cm._rewrite_display_in_place(None)
        print("display block removed")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
