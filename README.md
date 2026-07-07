# camlab

[![CI](https://github.com/Kurokesu/camlab-rpi/actions/workflows/ci.yml/badge.svg)](https://github.com/Kurokesu/camlab-rpi/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Kurokesu/camlab-rpi?include_prereleases&label=release)](https://github.com/Kurokesu/camlab-rpi/releases)
![OS](https://img.shields.io/badge/OS-RPi%20Trixie%20Lite-blue?logo=raspberrypi&logoColor=c51a4a)
![HW](https://img.shields.io/badge/HW-Pi%205%20%7C%20CM5-blue?logo=raspberrypi&logoColor=c51a4a)
![Sensors](https://img.shields.io/badge/sensors-AR0234%20%7C%20AR0822%20%7C%20IMX283%20%7C%20IMX462%20%7C%20IMX477%20%7C%20IMX585-blue)

Kiosk app for previewing and testing Kurokesu camera modules on Raspberry Pi.

## Setup

### Prepare Raspberry Pi

Flash Raspberry Pi OS Lite (Trixie 64-bit) to an SD card using [Raspberry Pi Imager](https://www.raspberrypi.com/software/):

- Select your Raspberry Pi device: **Raspberry Pi 5**
- Choose operating system: **Raspberry Pi OS (other)** --> **Raspberry Pi OS Lite (64-bit)**
- OS customization: set hostname, username and password. Enable SSH to run install remotely

Connect and boot:

- Attach HDMI display, keyboard and/or mouse
- Insert SD card and power on your Pi

> [!NOTE]
> App needs a display and one input device. Keyboard also makes SSH optional: all remaining steps work from the console.

Log in on the console or over SSH (`ssh <username>@<hostname>`), update OS and reboot:

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
> Device auto-reboots once more to init read-only root, app starts automatically on boot.

> [!NOTE]
> First time **camlab** starts with sensor defaults (AR0234 on `cam1`). Open **Select sensor** --> pick your camera and CSI port. Choice persists across reboots.

> [!WARNING]
> Connect or swap camera modules only when Pi is powered off and unplugged.

## Install details

By default `install.sh`:

- Enables [Kurokesu apt archive](https://apt.kurokesu.com) 
- Installs Kurokesu libcamera/rpicam-apps forks
- Installs Kurokesu sensor drivers
- Copies app to `/opt/camlab`
- Enables kiosk service
- Locks root read-only on next reboot

Optional flags:

- `--port=cam0` set CSI port to `cam0`. Defaults to `cam1`, switchable later in the GUI.
- `--no-readonly` keep root filesystem writable, for development.

## Development

Development and debugging notes - [DEVELOPMENT.md](DEVELOPMENT.md).
