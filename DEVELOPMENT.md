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

Network toggle (GUI Settings or `camlabctl net`) persists across reboots. Turning it off drops Wi-Fi immediately, Ethernet on next reboot (an SSH session over Ethernet survives as a grace period). Reverse from console or GUI.

## Installing from a clone

`sudo ./install.sh` from a checkout copies that tree to `/opt/camlab` and runs the same wiring as `camlab-setup`, no package needed. Add `--no-readonly` to keep root writable. Where the package is installed, the copy sits on top of package files until the next update restores them, and `dpkg -V camlab-rpi` lists what drifted.

`sudo scripts/setup/app-deploy.sh && camlabctl restart` redeploys code without re-running full setup.

## Running the app

Run directly under a Cage session with `python3 -m camlab`. Sensors are defined in `camlab/data/sensors.yaml`. CSI port is set in a managed block in `/boot/firmware/config.txt`. Boot is tuned by `scripts/setup/boot.sh` (run during install, `--revert` undoes it). Each script under `scripts/setup/` is self-documenting (`--help`) and safe to re-run.

## Panel preview

`CAMLAB_SCREEN=800x480` renders the UI panel-sized on a black backdrop, for judging a touch panel layout on a monitor. Set it with a drop-in:

```bash
sudo mkdir -p /etc/systemd/system/camlab.service.d
printf '[Service]\nEnvironment=CAMLAB_SCREEN=800x480\n' | sudo tee /etc/systemd/system/camlab.service.d/preview.conf
sudo systemctl daemon-reload && camlabctl restart
```

Remove the drop-in to return to full-screen rendering:

```bash
sudo rm -r /etc/systemd/system/camlab.service.d
sudo systemctl daemon-reload && camlabctl restart
```

## Read-only root

Root is read-only (overlayfs, RAM upper) so a yanked power cable can't corrupt it. `scripts/setup/readonly.sh` sets it up during install and arms a one-shot that locks down on the first reboot after first-boot tasks settle, so the operator does nothing extra. Sensor selections persist on a small loopback data partition at `/var/lib/camlab`, outside the overlay. For edits: `camlabctl rw`, reboot, change, `camlabctl ro`, reboot.

## Debian packaging

The deb recipe and its CI/release workflows are maintained on the `debian/latest` branch, separate from app source (DEP-14). `debian/source/README.source` there documents the layout, RELEASING.md here documents cutting a deb release.

## Boot storage

README walkthrough targets SD on a Pi 5, any boot storage works. CM5 eMMC boots fastest, ~15 s power-on to viewfinder vs ~21 s from SD on the bench. NVMe was tested and brings no benefit: it boots ~1 s slower than eMMC (from the NVMe controller init the eMMC fast-path skips) and the app needs neither the capacity nor the bandwidth.

## Fonts

`camlab/assets/MaterialSymbolsOutlined.ttf` is a subset of Google's Material Symbols with only glyphs specified in `camlab/gui/icons.py`. After adding a codepoint there, regenerate with `scripts/dev/icon-font.sh`.

`camlab/assets/Roboto-Regular.ttf` and `Roboto-Medium.ttf` are latin subsets of Google's Roboto, loaded as the application font by `camlab/gui/fonts.py` and read off disk by the boot splash. Bundled rather than installed: every box renders the same, and Debian ships a 2017 snapshot. Regenerate with `scripts/dev/text-font.sh`, which holds the subset ranges.

## Environment variables

- `CAMLAB_CAMERA_NUM` (default `0`)
- `CAMLAB_STATE_FILE` persisted mode/fps settings path
- `CAMLAB_NO_CAPTURE` disable stderr splicing
- `CAMLAB_SCREEN` force `WxH` panel preview (see [Panel preview](#panel-preview))
