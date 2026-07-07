# Development

Notes for developing and debugging camlab.

## Service control

```bash
camlabctl status               # print service state
camlabctl start|stop           # start/stop kiosk service
camlabctl restart              # restart
camlabctl logs -f              # tail service logs
camlabctl shot                 # screenshot live kiosk (needs grim)
camlabctl log-level debug      # set log level (follow with camlabctl restart)
camlabctl net off|on|status    # toggle networking
camlabctl rw                   # boot writable next time
camlabctl ro                   # boot read-only next time
```

Network toggle (GUI Settings or `camlabctl net`) takes effect immediately and persists across reboots. Turning it off drops SSH. Reverse from console or GUI.

## Running the app

Run directly under a Cage session with `python3 -m camlab`. Sensors live in `camlab/sensors.yaml`. CSI port lives in a managed block in `/boot/firmware/config.txt`. Boot is tuned by `scripts/setup/boot.sh` (run during install, `--revert` undoes it). Each script under `scripts/setup/` is self-documenting (`--help`) and safe to re-run.

## Read-only root

Root is read-only (overlayfs, RAM upper) so a yanked power cable can't corrupt it. `scripts/setup/readonly.sh` sets it up during install and arms a one-shot that locks down on the first reboot after first-boot tasks settle, so the operator does nothing extra. Sensor selections persist on a small loopback data partition at `/var/lib/camlab`, outside the overlay. For edits: `camlabctl rw`, reboot, change, `camlabctl ro`, reboot.

## Boot storage

Ships from eMMC. NVMe was tested and dropped: it boots ~1s slower (~16s vs ~15s power-on to preview, from the NVMe controller init the CM5 eMMC fast-path skips) and the app needs neither the capacity nor the bandwidth.

## Environment variables

- `CAMLAB_CAMERA_NUM` (default `0`)
- `CAMLAB_DISPLAY_MAX_FPS` display fps ceiling (default: screen refresh, capped at 60)
- `CAMLAB_BUFFER_COUNT` preview buffers per stream (default `4`)
- `CAMLAB_STATE_FILE` persisted mode/fps settings path
- `CAMLAB_NO_REBOOT` apply config without rebooting
- `CAMLAB_NO_CAPTURE` disable stderr splicing
- `CAMLAB_QT_BINDING` `pyqt5` (default) or `pyqt6`
- `QT_QPA_PLATFORM` defaults to `wayland` under a Wayland session
