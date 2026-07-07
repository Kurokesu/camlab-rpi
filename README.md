# camlab

[![CI](https://github.com/Kurokesu/camlab-rpi/actions/workflows/ci.yml/badge.svg)](https://github.com/Kurokesu/camlab-rpi/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Kurokesu/camlab-rpi?include_prereleases&label=release)](https://github.com/Kurokesu/camlab-rpi/releases)
![OS](https://img.shields.io/badge/OS-RPi%20Trixie%20Lite-blue?logo=raspberrypi&logoColor=c51a4a)
![HW](https://img.shields.io/badge/HW-Pi%205%20%7C%20CM5-blue?logo=raspberrypi&logoColor=c51a4a)
![Sensors](https://img.shields.io/badge/sensors-AR0234%20%7C%20AR0822%20%7C%20IMX283%20%7C%20IMX462%20%7C%20IMX477%20%7C%20IMX585-blue)

Kiosk app for previewing and testing Kurokesu camera modules on Raspberry Pi.

## Setup

### Operating system

Flash Raspberry Pi OS Lite (Trixie 64-bit) to an SD card using [Raspberry Pi Imager](https://www.raspberrypi.com/software/):

- Select your Raspberry Pi device: **Raspberry Pi 5**
- Choose operating system: **Raspberry Pi OS (other)** --> **Raspberry Pi OS Lite (64-bit)**
- OS customization: set hostname, username and password. Enable SSH to run install remotely

Boot:

- Insert SD card and power on your Pi
- Attach HDMI display, keyboard and/or mouse

> [!NOTE]
> App needs a display and one input device. Keyboard also makes SSH optional: all remaining steps work from the console.

- Log in on the console or over SSH (`ssh <username>@<hostname>`)
- Update OS and reboot:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

### Install camlab

Download latest [release](https://github.com/Kurokesu/camlab-rpi/releases) and install:

```bash
wget https://github.com/Kurokesu/camlab-rpi/releases/latest/download/camlab-rpi.zip
unzip camlab-rpi.zip && cd camlab-rpi
sudo ./install.sh
```

Reboot when install finishes:

```bash
sudo reboot
```

> [!NOTE]
> Device auto-reboots once more to init read-only root, app starts automatically.

`install.sh` adds Kurokesu camera stack, sensor drivers, overlay config and kiosk service. CSI port defaults to `cam1` (override with `--port=cam0`) and stays switchable in the GUI. App is copied to `/opt/camlab` and runs from there.

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

Run directly under a Cage session with `python3 -m camlab`. Sensors live in `camlab/sensors.yaml`. CSI port lives in a managed block in `/boot/firmware/config.txt`. Boot is tuned by `scripts/setup/boot.sh` (run during install, `--revert` undoes it). Each script under `scripts/setup/` is self-documenting (`--help`) and safe to re-run.

Ships from eMMC. NVMe was tested and dropped: it boots ~1s slower (~16s vs ~15s power-on to preview, from the NVMe controller init the CM5 eMMC fast-path skips) and the app needs neither the capacity nor the bandwidth.

Root is read-only (overlayfs, RAM upper) so a yanked power cable can't corrupt it. `scripts/setup/readonly.sh` sets it up during install and arms a one-shot that locks down on the first reboot after first-boot tasks settle, so the operator does nothing extra. Sensor selections persist on a small loopback data partition at `/var/lib/camlab`, outside the overlay. For edits: `camlabctl rw`, reboot, change, `camlabctl ro`, reboot.

### Environment variables

- `CAMLAB_CAMERA_NUM` (default `0`)
- `CAMLAB_DISPLAY_MAX_FPS` display fps ceiling (default: screen refresh, capped at 60)
- `CAMLAB_BUFFER_COUNT` preview buffers per stream (default `4`)
- `CAMLAB_STATE_FILE` persisted mode/fps settings path
- `CAMLAB_NO_REBOOT` apply config without rebooting
- `CAMLAB_NO_CAPTURE` disable stderr splicing
- `CAMLAB_QT_BINDING` `pyqt5` (default) or `pyqt6`
- `QT_QPA_PLATFORM` defaults to `wayland` under a Wayland session
