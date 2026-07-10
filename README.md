# Kurokesu camlab

[![CI](https://github.com/Kurokesu/camlab-rpi/actions/workflows/ci.yml/badge.svg)](https://github.com/Kurokesu/camlab-rpi/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/Kurokesu/camlab-rpi?include_prereleases&label=release)](https://github.com/Kurokesu/camlab-rpi/releases)
![OS](https://img.shields.io/badge/OS-RPi%20Trixie%20Lite-lightgrey?logo=raspberrypi&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Pi%205%20%7C%20CM5-lightgrey?logo=raspberrypi&logoColor=white)
![Sensors](https://img.shields.io/badge/sensors-AR0234%20%7C%20AR0822%20%7C%20IMX283%20%7C%20IMX462%20%7C%20IMX477%20%7C%20IMX585-lightgrey)

Kiosk app for previewing and testing Kurokesu camera modules on Raspberry Pi.

## Setup

### Prepare Raspberry Pi

Flash Raspberry Pi OS Lite (Trixie 64-bit) to an SD card using [Raspberry Pi Imager](https://www.raspberrypi.com/software/):

- Select your Raspberry Pi device: **Raspberry Pi 5**
- Choose operating system: **Raspberry Pi OS (other)** --> **Raspberry Pi OS Lite (64-bit)**
- OS customization: set hostname, username and password. Enable SSH to install remotely. Configure Wi-Fi unless using Ethernet

> [!NOTE]
> SSH is optional. With a keyboard every step also works from the console.

Connect and boot:

- Connect your camera module to either CSI port
- Attach HDMI display, keyboard and/or mouse
- Connect Ethernet, unless Wi-Fi was configured in Imager (install needs internet)
- Insert SD card and power on your Pi

> [!WARNING]
> Connect or swap camera modules only when Pi is powered off and unplugged.

> [!NOTE]
> App needs a display and one input device.

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

Start **camlab** when install finishes:

```bash
sudo systemctl start camlab
```

*App starts with sensor defaults (AR0234 on `cam1`) and no live image, sensor overlay loads on next boot.*

Open **Select sensor** --> pick your camera and CSI port --> **Apply & Shutdown**.

Once Pi powers off, power it back on. App starts automatically on boot, choices persist across reboots.

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
