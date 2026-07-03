# camlab

Bench tool for validating Kurokesu RPi camera modules.

> [!NOTE]
> Target: Raspberry Pi CM5 + IO board (or a Pi 5), Raspberry Pi OS Lite Trixie (64-bit).

## Setup

1. Flash Raspberry Pi OS Lite Trixie (64-bit) and boot.
2. Update the OS, then reboot:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

3. Download the latest release zip from Gitea and copy it to the rpi (from your host):

```bash
scp <version>.zip <username>@<hostname>:~/
```

4. Unzip and install (on the rpi):

```bash
ssh <username>@<hostname>
unzip <version>.zip && cd camlab-rpi
sudo ./install.sh
```

5. Reboot. Device auto-reboots once more on its own to init the read-only root, then camlab application starts automatically.

```bash
sudo reboot
```

`install.sh` adds Kurokesu camera stack, sensor drivers, overlay config and kiosk service. Prompts for CSI port (`cam0`/`cam1`). Pass `--port=cam0` to skip.

## Development

Service control and support:

```bash
camlabctl status
camlabctl logs -f
camlabctl restart
camlabctl shot                 # screenshot the live kiosk (needs grim)
camlabctl log-level debug      # then: camlabctl restart
camlabctl net off|on|status    # toggle networking (off for production)
camlabctl rw                   # boot writable next time (for edits)
camlabctl ro                   # boot read-only next time (production)
```

Networking is reversible: reach the rig over SSH during setup, ship it with no network. `camlabctl net off` drops the connection immediately. Reverse from the console with `camlabctl net on`.

Run directly under a Cage session with `python3 -m camlab`. Sensors live in `camlab/sensors.yaml`. CSI port lives in a managed block in `/boot/firmware/config.txt`. Boot is tuned by `scripts/setup/boot.sh` (run during install, `--revert` undoes it). Each script under `scripts/setup/` is idempotent and self-documenting (`--help`).

Ships from eMMC. NVMe was tested and dropped: it boots ~1s slower (~16s vs ~15s power-on to preview, from the NVMe controller init the CM5 eMMC fast-path skips) and the bench tool needs neither the capacity nor the bandwidth.

Root is read-only (overlayfs, RAM upper) so a yanked power cable can't corrupt it. `scripts/setup/readonly.sh` sets it up during install and arms a one-shot that locks down on the first reboot after first-boot tasks settle, so the operator does nothing extra. Sensor selections persist on a small loopback data partition at `/var/lib/camlab`, outside the overlay. For edits: `camlabctl rw`, reboot, change, `camlabctl ro`, reboot.

Useful env vars:

- `CAMLAB_CAMERA_NUM` (default `0`)
- `CAMLAB_DISPLAY_MAX_FPS` display fps ceiling (default: screen refresh, capped at 60)
- `CAMLAB_BUFFER_COUNT` preview buffers per stream (default `4`)
- `CAMLAB_STATE_FILE` persisted mode/fps settings path
- `CAMLAB_NO_REBOOT` apply config without rebooting
- `CAMLAB_NO_CAPTURE` disable stderr splicing
- `CAMLAB_QT_BINDING` `pyqt5` (default) or `pyqt6`
- `QT_QPA_PLATFORM` defaults to `wayland` under a Wayland session
