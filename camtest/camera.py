"""CameraEngine - thin wrapper over Picamera2 for the bench preview.

Owns the Picamera2 instance, configures a preview stream, builds the Qt preview
widget, and exposes detection + a first-frame hook (for boot-to-preview timing).
Designed to degrade gracefully: if no camera enumerates, the GUI still comes up
and reports the fact.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .qt import preview_widget_class

log = logging.getLogger(__name__)


@dataclass
class CameraInfo:
    model: str = ""
    id: str = ""
    location: str = ""
    num: int = -1

    @classmethod
    def from_dict(cls, d: dict) -> "CameraInfo":
        return cls(
            model=str(d.get("Model", "")),
            id=str(d.get("Id", "")),
            location=str(d.get("Location", "")),
            num=int(d.get("Num", -1)),
        )


class CameraEngine:
    def __init__(self, size=(1280, 720), pixel_format="XBGR8888"):
        self.size = tuple(size)
        self.pixel_format = pixel_format
        self.picam2 = None
        self.info: CameraInfo | None = None
        self.main_config: dict = {}
        self.sensor_config: dict = {}
        self.sensor_mode: dict = {}
        self._first_frame_cb = None
        self._first_frame_seen = False

    @staticmethod
    def detect() -> list[CameraInfo]:
        from picamera2 import Picamera2
        return [CameraInfo.from_dict(d) for d in Picamera2.global_camera_info()]

    def open(self, camera_num: int = 0) -> None:
        from picamera2 import Picamera2
        infos = Picamera2.global_camera_info()
        if not infos:
            raise RuntimeError("no camera enumerated by libcamera")
        self.info = CameraInfo.from_dict(infos[camera_num])
        self.picam2 = Picamera2(camera_num)
        # Reading sensor_modes the first time reconfigures the camera as a side
        # effect: picamera2 probes every raw mode with configure() and leaves the
        # camera on the last one, whose main stream falls back to the 640x480
        # default (4:3). Warm that cache here, BEFORE we apply our own preview
        # config, so our 1280x720 (16:9) configuration is the one that sticks.
        _ = self.picam2.sensor_modes
        cfg = self.picam2.create_preview_configuration(
            main={"size": self.size, "format": self.pixel_format})
        self.picam2.configure(cfg)
        full = self.picam2.camera_configuration()
        self.main_config = dict(full["main"])
        # The "sensor" config is the actual mode libcamera selected (bit depth +
        # output size). The "main"/"raw" formats are the ISP output / PiSP
        # internal formats (XBGR8888 / *_PISP_COMP*), which do NOT match what
        # rpicam-hello --list-cameras reports. Resolve the real sensor mode so
        # the GUI shows the same thing (e.g. SGRBG12_CSI2P 1920x1080).
        self.sensor_config = dict(full.get("sensor", {}))
        self.sensor_mode = self._match_sensor_mode(self.sensor_config)
        log.info("camera opened: %s (%s) sensor_mode=%s main=%s",
                 self.info.model, self.info.id,
                 self.sensor_mode_str(), self.main_config)

    def _match_sensor_mode(self, sensor_cfg: dict) -> dict:
        """Find the sensor_modes entry matching the configured size + bit depth.

        Its 'format' is the libcamera name rpicam-hello prints (e.g. SGRBG12_CSI2P).
        """
        size = tuple(sensor_cfg.get("output_size", ()) or ())
        depth = sensor_cfg.get("bit_depth")
        for m in (self.picam2.sensor_modes if self.picam2 else []):
            if tuple(m.get("size", ()) or ()) == size and m.get("bit_depth") == depth:
                return {
                    "format": str(m.get("format", "")),
                    "bit_depth": m.get("bit_depth"),
                    "size": tuple(m.get("size", ()) or ()),
                    "fps": m.get("fps"),
                }
        return {}

    def sensor_mode_str(self) -> str:
        """Human sensor mode matching rpicam-hello, e.g. 'SGRBG12_CSI2P 1920x1080'."""
        m = self.sensor_mode
        if m and m.get("format") and m.get("size"):
            w, h = m["size"]
            return f"{m['format']} {w}x{h}"
        size = tuple(self.sensor_config.get("output_size", ()) or ())
        depth = self.sensor_config.get("bit_depth")
        if size and depth:
            return f"{depth}-bit {size[0]}x{size[1]}"
        return "?"

    def make_preview_widget(self, software=False):
        # IMPORTANT: create the widget WITHOUT a parent. QGlPicamera2 allocates its
        # EGL window surface in __init__ via winId(). A top-level widget gets a valid
        # native X window, while an unrealized child parent yields EGL_BAD_ALLOC. The
        # layout reparents it afterwards (matches the working Phase 1 proto).
        cls = preview_widget_class(software=software)
        w, h = self.size
        widget = cls(self.picam2, width=w, height=h, keep_ar=True)
        return widget

    def on_first_frame(self, callback) -> None:
        """Register a one-shot callback(boottime_s) fired on the first captured frame."""
        self._first_frame_cb = callback

    def _pre_callback(self, request) -> None:
        if not self._first_frame_seen:
            self._first_frame_seen = True
            boottime = time.clock_gettime(time.CLOCK_BOOTTIME)
            if self._first_frame_cb:
                try:
                    self._first_frame_cb(boottime)
                except Exception:  # never let UI timing break capture
                    log.exception("first-frame callback failed")

    def start(self) -> None:
        if self.picam2 is None:
            raise RuntimeError("camera not opened")
        self.picam2.pre_callback = self._pre_callback
        self.picam2.start()

    def stop(self) -> None:
        if self.picam2 is not None:
            try:
                self.picam2.stop()
            except Exception:
                log.exception("camera stop failed")

    def close(self) -> None:
        if self.picam2 is not None:
            try:
                self.picam2.close()
            except Exception:
                log.exception("camera close failed")
            self.picam2 = None
