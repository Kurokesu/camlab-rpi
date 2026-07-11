# Kurokesu camlab

[![CI](https://github.com/Kurokesu/camlab-rpi/actions/workflows/ci.yml/badge.svg)](https://github.com/Kurokesu/camlab-rpi/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Kurokesu/camlab-rpi?include_prereleases&label=release)](https://github.com/Kurokesu/camlab-rpi/releases)
![OS](https://img.shields.io/badge/OS-RPi%20Trixie%20Lite-blue?logo=raspberrypi&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Pi%205%20%7C%20CM5-blue?logo=raspberrypi&logoColor=white)
![GUI](https://img.shields.io/badge/GUI-PyQt6-41cd52?logo=qt&logoColor=white)

![onsemi AR0234](https://img.shields.io/badge/onsemi-AR0234-008E9B?style=flat-square)
![onsemi AR0822](https://img.shields.io/badge/onsemi-AR0822-008E9B?style=flat-square)
![Sony IMX283](https://img.shields.io/badge/Sony-IMX283-008E9B?style=flat-square)
![Sony IMX462](https://img.shields.io/badge/Sony-IMX462-008E9B?style=flat-square)
![Sony IMX477](https://img.shields.io/badge/Sony-IMX477-008E9B?style=flat-square)
![Sony IMX585](https://img.shields.io/badge/Sony-IMX585-008E9B?style=flat-square)

*Camera modules built on these sensors are available at [kurokesu.com](https://www.kurokesu.com/item/CAM-CSI).*

Kiosk app for previewing and testing MIPI CSI camera modules on Raspberry Pi.

![camlab GUI](https://raw.githubusercontent.com/Kurokesu/camlab-rpi/main/docs/hero.png)

## Setup

*camlab runs on Raspberry Pi 5 or CM5. Any RAM size works, 2 GB is enough.*

### Prepare Raspberry Pi

Flash Raspberry Pi OS Lite (Trixie 64-bit) to an SD card using [Raspberry Pi Imager](https://www.raspberrypi.com/software/):

- Select your Raspberry Pi device: **Raspberry Pi 5**
- Choose operating system: **Raspberry Pi OS (other)** --> **Raspberry Pi OS Lite (64-bit)**
- OS customization: set hostname, username and password. Enable SSH to install remotely. Configure Wi-Fi unless using Ethernet

> [!NOTE]
> SSH is optional. With a keyboard every step also works from the console.

Connect and boot:

- Connect your camera module to either CSI port
- Attach HDMI display (1920×1080 recommended, other resolutions untested)
- Connect keyboard and/or mouse
- Connect Ethernet, unless Wi-Fi was configured in Imager (install needs internet)
- Insert SD card and power on your Pi

> [!WARNING]
> Connect or swap camera modules only when Pi is powered off and unplugged.

> [!NOTE]
> App needs a display and one input device.

- Log in on the console or over SSH (`ssh <username>@<hostname>`), update OS and reboot:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

### Install camlab

- Download latest [release](https://github.com/Kurokesu/camlab-rpi/releases) and install:

```bash
curl -#LO https://github.com/Kurokesu/camlab-rpi/releases/download/v1.0.0-beta.1/camlab-rpi.zip
unzip camlab-rpi.zip && cd camlab-rpi
sudo ./install.sh
```

- Start **camlab** when install finishes:

```bash
sudo systemctl start camlab
```

*App starts with sensor defaults (AR0234 on `cam1`) and no live image, sensor overlay loads on next boot.*

- Open **Select sensor** --> pick your camera and CSI port --> **Apply & Shutdown**

- Once Pi powers off, power it back on. App starts automatically on boot, choices persist across reboots

> [!NOTE]
> First power-on auto-reboots once to init read-only root.

## Install details

By default `install.sh`:

- Enables [Kurokesu apt archive](https://apt.kurokesu.com)
- Installs Kurokesu libcamera fork
- Installs Kurokesu sensor drivers
- Removes unused packages (rpicam-apps stack, sibling kernel flavor)
- Copies app to `/opt/camlab`
- Enables kiosk service
- Locks root read-only on next reboot

Optional flags:

- `--no-readonly` keep root filesystem writable, for development.

## Development

Development and debugging notes - [DEVELOPMENT.md](DEVELOPMENT.md).
