# camtest

Bench tool for validating Kurokesu RPi camera modules.

> [!NOTE]
> Target: Raspberry Pi CM5 + IO board (or a Pi 5), Raspberry Pi OS Lite Trixie (64-bit).

## Setup

1. Flash Raspberry Pi OS Lite Trixie (64-bit) and boot.
2. Clone and install:

```bash
git clone http://git.kurokesu.internal/Kurokesu-Electronics/camtest-rpi.git
cd camtest-rpi
sudo ./install.sh
```

3. Reboot. Kiosk starts automatically.

```bash
sudo reboot
```

`install.sh` adds Kurokesu camera stack, sensor drivers, overlay config and kiosk service. Prompts for CSI port (`cam0`/`cam1`). Pass `--port=cam0` to skip.

## Development

Service control and support:

```bash
camtestctl status
camtestctl logs -f
camtestctl restart
camtestctl shot                 # screenshot the live kiosk (needs grim)
camtestctl log-level debug      # then: camtestctl restart
camtestctl net off|on|status    # toggle networking (off for production)
```

Networking is reversible: reach the rig over SSH during setup, ship it with no network. `camtestctl net off` drops the connection immediately. Reverse from the console with `camtestctl net on`.

Run directly under a Cage session with `python3 -m camtest`. Sensors live in `camtest/sensors.yaml`. CSI port lives in a managed block in `/boot/firmware/config.txt`. Boot is tuned by `scripts/setup/boot.sh` (run during install, `--revert` undoes it). Each script under `scripts/setup/` is idempotent and self-documenting (`--help`).

Useful env vars:

- `CAMTEST_CAMERA_NUM` (default `0`)
- `CAMTEST_DISPLAY_MAX_FPS` display fps ceiling (default: screen refresh, capped at 60)
- `CAMTEST_BUFFER_COUNT` preview buffers per stream (default `4`)
- `CAMTEST_STATE_FILE` persisted mode/fps settings path
- `CAMTEST_NO_REBOOT` apply config without rebooting
- `CAMTEST_NO_CAPTURE` disable stderr splicing
- `CAMTEST_QT_BINDING` `pyqt5` (default) or `pyqt6`
- `QT_QPA_PLATFORM` defaults to `wayland` under a Wayland session
