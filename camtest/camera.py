"""CameraEngine - thin wrapper over Picamera2 for the bench preview.

Owns the Picamera2 instance, enumerates the sensor's runtime-selectable modes,
configures a (raw + main + lores) preview pipeline, builds the Qt preview widget,
and exposes detection + a first-frame hook (for boot-to-preview timing). Designed
to degrade gracefully: if no camera enumerates, the GUI still comes up and reports
the fact.

Stream topology (per mode): the raw stream carries the selected sensor mode, the
main stream is the full-resolution ISP output (XBGR8888, exercises the pipeline),
and the lores stream (YUV420) is scaled to the on-screen preview and is what the
GL widget displays (display="lores"). The framerate is locked exactly by setting
FrameDurationLimits min == max.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

from .modes import (
    SensorMode,
    enumerate_modes,
    fps_to_frame_duration,
    plan_lores_size,
)
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
        self.size = tuple(size)            # lores / display size (set on configure)
        self.pixel_format = pixel_format
        self.picam2 = None
        self.info: CameraInfo | None = None
        self.modes: list[SensorMode] = []
        self.main_config: dict = {}
        self.lores_config: dict = {}
        self.sensor_config: dict = {}
        self.sensor_mode: dict = {}
        self.current_mode: SensorMode | None = None
        self.current_fps: float | None = None
        self.framerate = 0.0          # instantaneous fps (rpicam-style)
        self.last_metadata: dict = {}  # latest per-frame libcamera metadata
        self._last_ts = 0             # previous SensorTimestamp (ns), for fps
        self._started = False
        self._first_frame_cb = None
        self._first_frame_seen = False
        self._snap_cb = None          # one-shot freeze-frame grab (camera thread)

    @staticmethod
    def detect() -> list[CameraInfo]:
        from picamera2 import Picamera2
        return [CameraInfo.from_dict(d) for d in Picamera2.global_camera_info()]

    def open(self, camera_num: int = 0) -> None:
        """Open the camera and enumerate its modes. Does NOT configure a stream.

        Configuration is deferred to configure_mode() so the boot mode can be
        resolved against the persisted selection and the actual display size
        (which need QApplication). Reading sensor_modes here also warms the
        picamera2 cache: the first access probes every raw mode with configure()
        as a side effect and leaves the camera on the last probed (640x480)
        mode, so the very next configure() we issue must be the real one.
        """
        from picamera2 import Picamera2
        infos = Picamera2.global_camera_info()
        if not infos:
            raise RuntimeError("no camera enumerated by libcamera")
        self.info = CameraInfo.from_dict(infos[camera_num])
        self.picam2 = Picamera2(camera_num)
        self.modes = enumerate_modes(self.picam2.sensor_modes)
        log.info("camera opened: %s (%s) with %d modes",
                 self.info.model, self.info.id, len(self.modes))

    @staticmethod
    def _buffer_count() -> int:
        try:
            return max(1, int(os.environ.get("CAMTEST_BUFFER_COUNT", "4")))
        except ValueError:
            return 4

    def configure_mode(self, mode: SensorMode, fps: float, avail_size) -> None:
        """Configure raw + main + lores for a mode at a locked fps.

        avail_size is the on-screen preview area in pixels. It sizes the lores
        (display) stream to the largest size of the mode's aspect ratio that fits.
        """
        if self.picam2 is None:
            raise RuntimeError("camera not opened")
        main_size = tuple(mode.size)
        lores_size = plan_lores_size(main_size, tuple(avail_size))
        dur = fps_to_frame_duration(fps)
        cfg = self.picam2.create_preview_configuration(
            main={"size": main_size, "format": self.pixel_format},
            lores={"size": lores_size, "format": "YUV420"},
            sensor={"output_size": main_size, "bit_depth": int(mode.bit_depth)},
            display="lores",
            buffer_count=self._buffer_count(),
            controls={"FrameDurationLimits": (dur, dur)},
        )
        self.picam2.configure(cfg)
        full = self.picam2.camera_configuration()
        self.main_config = dict(full["main"])
        self.lores_config = dict(full.get("lores") or {})
        # The "sensor" config is the actual mode libcamera selected (bit depth +
        # output size). The "main"/"raw" formats are the ISP output / PiSP
        # internal formats (XBGR8888 / *_PISP_COMP*), which do NOT match what
        # rpicam-hello --list-cameras reports. Resolve the real sensor mode so
        # the GUI shows the same thing (e.g. SGRBG12_CSI2P 3840x2160).
        self.sensor_config = dict(full.get("sensor") or {})
        self.sensor_mode = self._match_sensor_mode(self.sensor_config)
        self.current_mode = mode
        self.current_fps = float(fps)
        self.size = tuple(self.lores_config.get("size", lores_size))
        log.info("configured: sensor_mode=%s fps=%.2f main=%s lores=%s",
                 self.sensor_mode_str(), fps,
                 self.main_config.get("size"), self.size)

    def apply_mode(self, mode: SensorMode, fps: float, avail_size) -> None:
        """Reconfigure to a new mode/fps while running (stop, configure, start)."""
        was_started = self._started
        if was_started:
            self.stop()
        self.configure_mode(mode, fps, avail_size)
        if was_started:
            self.start()

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

    def request_snapshot(self, callback) -> bool:
        """Ask for a one-shot freeze-frame grabbed from the next delivered frame.

        callback(pil_image) fires once on the camera thread. Grabbing the
        already-delivered frame instead of a separate capture_image avoids a
        pipeline round-trip, so the live preview does not hitch. Returns False if
        not running.
        """
        if self.picam2 is None or not self._started:
            return False
        self._snap_cb = callback
        return True

    def _pre_callback(self, request) -> None:
        # Runs on the camera thread for every delivered frame. Capture the latest
        # metadata (exposure, gains) and compute the instantaneous frame rate the
        # same way rpicam-apps does: 1e9 / the delta between consecutive
        # SensorTimestamps (nanoseconds). A dropped frame widens the delta and so
        # shows up as a lower rate. The GUI samples both for the readout.
        try:
            self.last_metadata = request.get_metadata()
        except Exception:  # never let telemetry break capture
            pass
        ts = self.last_metadata.get("SensorTimestamp")
        if ts is not None:
            if self._last_ts and ts != self._last_ts:
                self.framerate = 1e9 / (ts - self._last_ts)
            self._last_ts = ts
        if self._snap_cb is not None:
            cb, self._snap_cb = self._snap_cb, None
            try:
                # The main (ISP RGB) buffer: make_image can't convert YUV420 lores.
                cb(request.make_image("main"))
            except Exception:  # never let the freeze-frame break capture
                log.exception("freeze-frame grab failed")
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
        if self.current_mode is None:
            raise RuntimeError("camera not configured (call configure_mode first)")
        self.picam2.pre_callback = self._pre_callback
        self.picam2.start()
        self._started = True

    def stop(self) -> None:
        if self.picam2 is not None:
            try:
                self.picam2.stop()
            except Exception:
                log.exception("camera stop failed")
            finally:
                self._started = False

    def close(self) -> None:
        if self.picam2 is not None:
            try:
                self.picam2.close()
            except Exception:
                log.exception("camera close failed")
            self.picam2 = None
