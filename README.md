# camtest

Single-operator bench tool for validating Kurokesu RPi camera modules on a CM5 + IO board. Live preview, sensor selection, and signal-integrity surfacing, fullscreen under a Cage kiosk.

camtest is a test instrument, not a verdict engine. It surfaces facts (what enumerates, what mode runs, what errors the camera stack emits) and leaves pass/fail judgement to the operator.

> [!NOTE]
> Target: Raspberry Pi CM5 (or Pi 5) + IO board, Raspberry Pi OS Lite Trixie (64-bit, Debian 13), booted from eMMC. Needs the Kurokesu libcamera/rpicam-apps fork (`+krks`) from `apt.kurokesu.com`.

## Install

Full install (adds the Kurokesu archive, installs deps + sensor drivers, configures the overlay, enables the kiosk service):

```bash
sudo ./install.sh
```

Non-interactive port, or skip the Phase 5 overlay-root step:

```bash
sudo ./install.sh --port=cam0
sudo ./install.sh --no-readonly
```

Reboot to load the sensor overlay and boot into the kiosk:

```bash
sudo reboot
```

For partial reconfigures on a dev box, run any primitive under `scripts/setup/` directly. Each is idempotent and self-documenting (`--help`).

## What it shows

- **Live preview**, hardware-accelerated via `QGlPicamera2` (PyQt5 + OpenGL).
- **Status strip**: detected model, capture mode, instantaneous fps (computed rpicam-style from sensor timestamps), actual exposure + analogue/digital gain, sensor temperature (when the sensor reports it), boot-to-preview time.
- **Integrity indicator**: a live count + rate of camera-stack errors, prominent when non-zero (see below).
- **Sensor selector**: pick sensor + CSI port, then apply and reboot.
- **Mode selector**: pick resolution, bit depth, and fps at runtime; applied live and remembered across reboots.
- **Log panel**: collapsible view of the camera-stack stderr, integrity lines highlighted.

## Integrity surfacing

A known-good preview can still be silently degraded by cabling or signal-integrity faults. An over-length CSI cable, for example, produces no CFE CRC errors but makes the AR0822 `cam_helper` log every frame:

```
ERROR ONSEMI md_parser_onsemi.cpp:158 Incorrect register value tags at 169
ERROR IPARPI cam_helper.cpp:218 Embedded data buffer parsing failed
```

camtest splices the camera stack's stderr in-process (re-emitting to the journal, so nothing is lost), classifies each line against an editable pattern table (`camtest/integrity.py`), and surfaces matches as a running count + rolling rate. Errors are shown as facts, never a pass/fail verdict.

> [!TIP]
> Develop against a long-cable rig where errors fire continuously, then confirm the indicator goes clean with a known-good cable.

## Sensor selection

Sensors live in `camtest/sensors.yaml` (curated, human-maintained). Each entry maps a display name to a dt overlay token plus default options. The CSI port is a per-rig setting (default `cam0`), stored in a managed block in `/boot/firmware/config.txt`, not per sensor.

### cam0

Default port. Composes `dtoverlay=ar0822,cam0,4lane`.

### 4lane

Default for AR0822: 4-lane MIPI CSI. Listed as a default option in the registry.

Changing the sensor rewrites the managed block and reboots, since dt overlays are read at boot. Writes go through a single scoped shim (`/usr/local/bin/camtest-apply`) the GUI may `sudo` (see `deploy/camtest-sudoers`). Nothing else is privileged.

## Mode selection

The **Mode...** dialog lets the operator switch the running capture mode without a reboot. The choices come straight from what libcamera reports for the sensor (`camtest/modes.py`), presented as a dependent cascade:

- **Resolution** -> **Bit depth** -> **FPS**. Changing one reconciles the ones below it, so the UI can only ever offer a combination the hardware actually supports.
- **FPS policy**: the bench rates 30 and 60, capped by the sensor mode and the display. When a mode's maximum falls between the two (e.g. 4K runs at 33.89 or 40.03 fps), that exact maximum is offered as the top option. When only one rate fits, the selector locks. The chosen rate is held exactly via `FrameDurationLimits`.
- **Max-stress default**: with no saved selection, the heaviest runnable mode is picked (largest area, deepest bits, highest fps within the display limit), e.g. AR0822 4K / 12-bit / 33.89 fps.

Applying reconfigures the pipeline (raw + full-resolution main + a lores stream scaled to the preview area) and, only on success, persists the selection per sensor. State is written unprivileged to the service's `StateDirectory` (`/var/lib/camtest/state.json`); a missing or invalid entry falls back to the max-stress default.

## Development

Service control and field support go through `camtestctl`:

```bash
camtestctl status
camtestctl logs -f
camtestctl restart
camtestctl shot                 # screenshot the live kiosk (needs grim)
camtestctl log-level debug      # then: camtestctl restart
```

Run the app directly (under a Cage session) with `python3 -m camtest`. Useful env vars:

- `CAMTEST_CAMERA_NUM` (default `0`)
- `CAMTEST_DISPLAY_MAX_FPS` override the display fps ceiling (default: screen refresh, capped at 60)
- `CAMTEST_BUFFER_COUNT` preview buffers per stream (default `4`)
- `CAMTEST_STATE_FILE` override the persisted mode/fps settings path
- `CAMTEST_NO_REBOOT` apply config without rebooting (dev)
- `CAMTEST_NO_CAPTURE` disable stderr splicing (debug)
- `CAMTEST_QT_BINDING` `pyqt5` (default) or `pyqt6`
- `QT_QPA_PLATFORM` defaults to `wayland` under a Wayland session (maps fullscreen without the Xwayland small-window flash); set explicitly to force a platform
