# Kurokesu camlab

[![CI](https://github.com/Kurokesu/camlab-rpi/actions/workflows/ci.yml/badge.svg)](https://github.com/Kurokesu/camlab-rpi/actions/workflows/ci.yml)
[![REUSE status](https://api.reuse.software/badge/github.com/Kurokesu/camlab-rpi)](https://api.reuse.software/info/github.com/Kurokesu/camlab-rpi)
[![Release](https://img.shields.io/github/v/release/Kurokesu/camlab-rpi?include_prereleases&label=release)](https://github.com/Kurokesu/camlab-rpi/releases)
![OS](https://img.shields.io/badge/OS-RPi%20Trixie%20Lite-blue?logo=raspberrypi&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Pi%205%20%7C%20CM5-blue?logo=raspberrypi&logoColor=white)
![GUI](https://img.shields.io/badge/GUI-PyQt6-blue?logo=qt&logoColor=white)
[![libcamera](https://img.shields.io/badge/libcamera-Kurokesu%20fork-blue)](https://github.com/Kurokesu/libcamera)
[![picamera2](https://img.shields.io/badge/picamera2-0.3.36-blue)](https://github.com/raspberrypi/picamera2)
![Python](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)

Kiosk app for live preview and testing MIPI CSI camera modules on Raspberry Pi.

![onsemi AR0234](https://img.shields.io/badge/onsemi-AR0234-008E9B?style=flat-square)
![onsemi AR0822](https://img.shields.io/badge/onsemi-AR0822-008E9B?style=flat-square)
![Sony IMX283](https://img.shields.io/badge/Sony-IMX283-008E9B?style=flat-square)
![Sony IMX462](https://img.shields.io/badge/Sony-IMX462-008E9B?style=flat-square)
![Sony IMX477](https://img.shields.io/badge/Sony-IMX477-008E9B?style=flat-square)
![Sony IMX585](https://img.shields.io/badge/Sony-IMX585-008E9B?style=flat-square)

*Camera modules based on these sensors are available at [kurokesu.com](https://www.kurokesu.com/item/CAM-CSI)*

![camlab GUI](https://raw.githubusercontent.com/Kurokesu/camlab-rpi/main/docs/hero.png)

## Setup

*camlab runs on Raspberry Pi 5 or CM5. Any RAM size works, 2 GB is enough.*

### Prepare Raspberry Pi

Flash Raspberry Pi OS $\color{#CA2031}{\textbf{\textsf{Lite}}}$ (Trixie 64-bit) to an SD card using [Raspberry Pi Imager](https://www.raspberrypi.com/software/):

- Select your Raspberry Pi device: **Raspberry Pi 5**
- Choose operating system: **Raspberry Pi OS (other)** --> **Raspberry Pi OS Lite (64-bit)**
- OS customization: set hostname, username and password. Enable SSH to install remotely. Configure Wi-Fi unless using Ethernet

> [!NOTE]
> SSH is optional. With a keyboard every step also works from the console.

Connect and boot:

- Connect your camera module to either CSI port (CM5 needs extra steps, see [CM5 IO board](#cm5-io-board))
- Attach HDMI display (1920×1080 recommended, other resolutions untested) or a DSI touch panel (see [Touch display](#touch-display))
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

- Enable [Kurokesu apt archive](https://apt.kurokesu.com):

```bash
curl -fsSLO https://apt.kurokesu.com/setup.sh
sudo sh setup.sh --update
```

- Install camlab and set up kiosk:

```bash
# skip desktop extras that picamera2 recommends
sudo apt install --no-install-recommends camlab-rpi
sudo camlab-setup
```

> [!NOTE]
> Installing from apt is what makes in-app updates work. A copy unpacked by hand gets none.

- Start **camlab** when install finishes:

```bash
sudo systemctl start camlab
```

*App starts with sensor defaults (AR0234 on a free CSI port) and no live image, sensor overlay loads on next boot.*

- Open **Select sensor** --> pick your camera and CSI port --> **Apply & Shutdown**

- Once Pi powers off, power it back on. App starts automatically on boot, choices persist across reboots

> [!NOTE]
> First power-on auto-reboots once to init read-only root.

## Install details

By default `camlab-setup`:

- Enables [Kurokesu apt archive](https://apt.kurokesu.com)
- Installs Kurokesu libcamera fork
- Installs Kurokesu sensor drivers
- Removes sibling kernel flavor, so drivers build once
- Enables kiosk service
- Locks root read-only on next reboot

Optional flags:

- `--no-readonly` keep root filesystem writable, for development.
- `--display <overlay>` enable a DSI touch panel on CM5 (see [Touch display](#touch-display)).

## CM5 IO board

Install flow matches Pi 5, with these differences:

- On eMMC variants, flash OS to eMMC using [usbboot](https://github.com/raspberrypi/usbboot). CM5 Lite boots from SD card as on Pi 5
- Fit both J6 jumpers (route I2C to `CAM/DISP1`)
- Default wiring: camera on `CAM/DISP0` (`cam0` in **Select sensor**), optional touch panel on `CAM/DISP1`. Swap them with `--display vc4-kms-dsi-7inch,dsi0`

## Touch display

A DSI touch panel serves as both display and input device. Supported panels:

- [Waveshare 43H](https://www.waveshare.com/4.3inch-dsi-lcd.htm) 800×480 (overlay `vc4-kms-dsi-7inch`)

Setup:

- Pi 5: plug and play, firmware loads overlay and **Select sensor** shows touch display as auto-detected
- CM5: pick it in **Select sensor** dialogue together with the sensor, one **Apply & Shutdown** covers both
- CM5 without HDMI: install with `--display vc4-kms-dsi-7inch`, reboot and continue sensor selection on touch display

## Development

Development and debugging notes - [DEVELOPMENT.md](DEVELOPMENT.md).
