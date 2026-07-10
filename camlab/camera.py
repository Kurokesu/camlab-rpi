"""CameraEngine - thin wrapper over Picamera2 for the bench viewfinder.

Owns Picamera2 instance, enumerates sensor's runtime-selectable modes,
configures a (raw + main + lores) pipeline, builds the Qt viewfinder widget,
holds exposure/gain/WB control state and exposes detection + a first-frame
hook (for boot-to-viewfinder timing). Designed to degrade gracefully: if no
camera enumerates, GUI still comes up and reports "no camera".

Stream topology (per mode): the raw stream carries the selected sensor mode,
main stream is the full-resolution ISP output (XBGR8888, exercises the pipeline)
and lores stream (YUV420) is scaled to the on-screen viewfinder and is what
the GL widget displays (display="lores"). The framerate is locked exactly by
setting FrameDurationLimits min == max.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field

from picamera2 import Picamera2

from .gl_viewfinder import GlViewfinder
from .modes import (
    SensorMode,
    enumerate_modes,
    fps_to_frame_duration,
    plan_lores_size,
)

log = logging.getLogger(__name__)

# libcamera advertises ColourTemperature as 100-100000 K, far beyond any tuning
# curve. Clamp slider range to a practical photographic band.
_CT_UI_RANGE = (2000, 10000)

# Sentinel so set_control_state can tell "not passed" from "None = auto".
_UNSET = object()


@dataclass
class ControlState:
    """Manual control overrides, None means auto.

    Exposure, gain and white balance are independently auto or manual, matching
    libcamera's split AE API. Values clamp to the current mode's advertised
    range on every set and on mode change.
    """
    exposure_us: int | None = None
    gain: float | None = None
    colour_temp: int | None = None


@dataclass(frozen=True)
class Telemetry:
    """One frame's live readout, published by the camera thread for the GUI.

    The camera thread builds a fresh instance per frame and swaps it into
    CameraEngine.telemetry in a single attribute assignment. The GUI reads that
    one reference, so it always gets #frame, fps and metadata from the same
    frame without locking (the swap is atomic under CPython).
    """
    frame: int | None = None  # None until a frame has been captured
    fps: float = 0.0
    metadata: dict = field(default_factory=dict)


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
        self.control_state = ControlState()
        self.telemetry = Telemetry()  # latest per-frame snapshot (camera -> GUI)
        self._last_ts = 0             # previous SensorTimestamp (ns), for fps
        self._started = False
        self._first_frame_cb = None
        self._first_frame_seen = False

    def open(self, camera_num: int = 0) -> None:
        """Open the camera and enumerate its modes. Does NOT configure a stream.

        Configuration is deferred to configure_mode() so the boot mode can be
        resolved against the persisted selection and the actual display size
        (which need QApplication). Reading sensor_modes here also warms the
        picamera2 cache: the first access probes every raw mode with configure()
        as a side effect and leaves the camera on the last probed (640x480)
        mode, so the very next configure() we issue must be the real one.
        """
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
            return max(1, int(os.environ.get("CAMLAB_BUFFER_COUNT", "4")))
        except ValueError:
            return 4

    def configure_mode(self, mode: SensorMode, fps: float, avail_size) -> None:
        """Configure raw + main + lores for a mode at a locked fps.

        avail_size is the on-screen viewfinder area in pixels. It sizes lores
        (display) stream to the largest size of mode's aspect ratio that fits.
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
        # The main/raw formats are ISP/PiSP internal (XBGR8888 / *_PISP_COMP*) and
        # do not match what rpicam-hello --list-cameras reports, so resolve the
        # actual sensor mode libcamera selected for the GUI to display.
        self.sensor_config = dict(full.get("sensor") or {})
        self.sensor_mode = self._match_sensor_mode(self.sensor_config)
        self.current_mode = mode
        self.current_fps = float(fps)
        self.size = tuple(self.lores_config.get("size", lores_size))
        # configure() resets picam2.controls to the config's, so re-clamp
        # manual values against the new mode and push them again.
        self._clamp_control_state()
        self._apply_controls()
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

    # camera controls (exposure / gain / white balance)
    def control_ranges(self) -> dict[str, tuple]:
        """(min, max) per manual control for the current configuration.

        Keys mirror ControlState fields. A missing key means the camera does
        not offer that control (e.g. no ColourTemperature on mono sensors), so
        GUI hides it. Ranges come from camera_controls, except exposure max,
        which the locked fps caps at one frame duration.
        """
        if self.picam2 is None:
            return {}
        cc = self.picam2.camera_controls
        ranges: dict[str, tuple] = {}
        if "ExposureTime" in cc:
            lo, hi, _ = cc["ExposureTime"]
            if self.current_fps:
                hi = min(hi, fps_to_frame_duration(self.current_fps))
            ranges["exposure_us"] = (int(lo), int(hi))
        if "AnalogueGain" in cc:
            lo, hi, _ = cc["AnalogueGain"]
            ranges["gain"] = (float(lo), float(hi))
        if "ColourTemperature" in cc and "AwbEnable" in cc:
            lo, hi, _ = cc["ColourTemperature"]
            ranges["colour_temp"] = (max(int(lo), _CT_UI_RANGE[0]),
                                     min(int(hi), _CT_UI_RANGE[1]))
        return ranges

    def set_control_state(self, exposure_us=_UNSET, gain=_UNSET,
                          colour_temp=_UNSET) -> ControlState:
        """Update one or more controls (None = auto) and push them to libcamera.

        Values clamp to the current mode's range. Returns resulting state
        (caller reads back what was actually set, e.g. for persisting).
        """
        st = self.control_state
        if exposure_us is not _UNSET:
            v = self._clamped("exposure_us", exposure_us)
            st.exposure_us = int(v) if v is not None else None
        if gain is not _UNSET:
            v = self._clamped("gain", gain)
            st.gain = float(v) if v is not None else None
        if colour_temp is not _UNSET:
            v = self._clamped("colour_temp", colour_temp)
            st.colour_temp = int(v) if v is not None else None
        self._apply_controls()
        return st

    def _clamped(self, key: str, value):
        if value is None:
            return None
        rng = self.control_ranges().get(key)
        if rng is None:
            return None  # control not offered, stay auto
        return min(max(value, rng[0]), rng[1])

    def _clamp_control_state(self) -> None:
        """Re-clamp manual values against the new mode's ranges."""
        st = self.control_state
        for key in ("exposure_us", "gain", "colour_temp"):
            v = getattr(st, key)
            if v is not None:
                c = self._clamped(key, v)
                setattr(st, key, type(v)(c) if c is not None else None)

    def _apply_controls(self) -> None:
        if self.picam2 is None:
            return
        st = self.control_state
        ctrls: dict = {}
        # 0 = auto, 1 = manual (libcamera ExposureTimeMode/AnalogueGainMode,
        # split AE API, always present on our pinned libcamera).
        ctrls["ExposureTimeMode"] = 0 if st.exposure_us is None else 1
        if st.exposure_us is not None:
            ctrls["ExposureTime"] = int(st.exposure_us)
        ctrls["AnalogueGainMode"] = 0 if st.gain is None else 1
        if st.gain is not None:
            ctrls["AnalogueGain"] = float(st.gain)
        if "colour_temp" in self.control_ranges():
            ctrls["AwbEnable"] = st.colour_temp is None
            if st.colour_temp is not None:
                ctrls["ColourTemperature"] = int(st.colour_temp)
        if ctrls:
            self.picam2.set_controls(ctrls)

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

    def make_viewfinder(self):
        return GlViewfinder(self.picam2)

    def on_first_frame(self, callback) -> None:
        """Register a one-shot callback(boot_time_s) fired on the first captured frame."""
        self._first_frame_cb = callback

    def _pre_callback(self, request) -> None:
        # Camera thread, per delivered frame. fps is rpicam-style: 1e9 / the delta
        # between consecutive SensorTimestamps (ns), so a dropped frame reads as a
        # lower rate. #frame is the libcamera request sequence, not a metadata field.
        prev = self.telemetry
        lib_req = getattr(request, "request", None)
        frame = lib_req.sequence if lib_req is not None else prev.frame
        try:
            md = request.get_metadata()
        except Exception:  # keep the last metadata on a parse failure
            md = prev.metadata
        fps = prev.fps
        ts = md.get("SensorTimestamp")
        if ts is not None:
            if self._last_ts and ts != self._last_ts:
                fps = 1e9 / (ts - self._last_ts)
            self._last_ts = ts
        # Publish the frame's readout as one snapshot so the GUI reads a
        # consistent set (single atomic swap, no lock needed).
        self.telemetry = Telemetry(frame=frame, fps=fps, metadata=md)
        if not self._first_frame_seen:
            self._first_frame_seen = True
            boot_time = time.clock_gettime(time.CLOCK_BOOTTIME)
            if self._first_frame_cb:
                try:
                    self._first_frame_cb(boot_time)
                except Exception:  # never let UI timing break capture
                    log.exception("first-frame callback failed")

    def start(self) -> None:
        if self.picam2 is None:
            raise RuntimeError("camera not opened")
        if self.current_mode is None:
            raise RuntimeError("camera not configured (call configure_mode first)")
        # Fresh run: clear the last snapshot so a mode switch reads as a new
        # capture (libcamera resets the request sequence itself on start).
        self.telemetry = Telemetry()
        self._last_ts = 0
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
