# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""CameraEngine - thin wrapper over Picamera2 for the bench viewfinder.

Owns the Picamera2 instance, mode enumeration, control state and the
coalesced pipeline flush. Degrades gracefully: with no camera the GUI
still comes up and reports it.

Streams per mode: raw carries the sensor mode, main is the full-res ISP
output, lores (YUV420) feeds the GL viewfinder. Fixed FPS lock pins
FrameDurationLimits min == max, exposure driven widens the max toward 1 s.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import numpy as np
from picamera2 import Picamera2

from .gl_viewfinder import GlViewfinder
from .modes import (
    SensorMode,
    enumerate_modes,
    fps_to_frame_duration,
    plan_lores_size,
)
from .qt import QtCore

log = logging.getLogger(__name__)

# libcamera advertises ColourTemperature as 100-100000 K, far beyond any tuning
# curve. Clamp slider range to a practical photographic band.
_CT_UI_RANGE = (2000, 10000)

# Sentinel so set_control_state can tell "not passed" from "None = auto".
_UNSET = object()

# PispStatsOutput blob layout (libpisp pisp_statistics.h): the AGC luma
# histogram sits past the AWB zone block and the AGC row sums.
_AGC_HIST_OFFSET = 16448 + 2048
_AGC_HIST_BINS = 1024

# Frame duration ceiling when FPS is exposure driven
_MAX_FRAME_US = 1_000_000

# Flush controls when queued frames would add visible latency.
_SLOW_FRAME_US = 100_000


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
    """Atomic per-frame snapshot for GUI readers."""
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
        self.fps_current: float | None = None
        self.fps_fixed = True  # False lets exposure extend frame duration to 1 s
        self.control_state = ControlState()
        self.stats_output = False   # ISP statistics in metadata (histogram)
        # Latch histogram because stats arrive below frame rate.
        self.latest_histogram: np.ndarray | None = None
        self.telemetry = Telemetry()  # latest per-frame snapshot
        self._last_ts = 0             # previous SensorTimestamp (ns), for fps
        self._seq_base = 0            # frame counter offset, keeps it continuous across flushes
        self._frame_since_start = False
        self._start_ts = 0.0
        self._started = False
        self._first_frame_cb = None
        self._first_frame_seen = False
        self._flush_pending = False
        # Drain timer, created on first use (needs QApplication).
        self._flush_timer: QtCore.QTimer | None = None
        self._cc_cache: dict | None = None  # camera_controls, per configure

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
        self._cc_cache = None
        self.modes = enumerate_modes(self.picam2.sensor_modes)
        log.info("camera opened: %s (%s) with %d modes",
                 self.info.model, self.info.id, len(self.modes))

    @staticmethod
    def _buffer_count(fps: float) -> int:
        """Use eight buffers above 60 fps to absorb GUI pauses, four otherwise."""
        return 8 if fps > 60.5 else 4

    def configure_mode(self, mode: SensorMode, fps: float, avail_size,
                       fps_fixed: bool = True) -> None:
        """Configure mode streams and fit lores within avail_size.

        Selected FPS is fixed or the exposure-driven ceiling.
        """
        if self.picam2 is None:
            raise RuntimeError("camera not opened")
        self.fps_fixed = bool(fps_fixed)
        main_size = tuple(mode.size)
        lores_size = plan_lores_size(main_size, tuple(avail_size))
        dur = fps_to_frame_duration(fps)
        cfg = self.picam2.create_preview_configuration(
            main={"size": main_size, "format": self.pixel_format},
            lores={"size": lores_size, "format": "YUV420"},
            sensor={"output_size": main_size, "bit_depth": int(mode.bit_depth)},
            display="lores",
            buffer_count=self._buffer_count(fps),
            controls={"FrameDurationLimits": (dur, dur)},
        )
        self.picam2.configure(cfg)
        self._flush_pending = False  # reconfigure restarts the pipeline anyway
        # Control limits change with the mode (e.g. exposure scales with
        # line length), so the widen and re-clamp below must see fresh ones.
        self._cc_cache = None
        if not self.fps_fixed:
            if self._sensor_max_frame_us() is None:
                log.warning("no usable FrameDurationLimits from sensor, "
                            "FPS lock degrades to fixed")
                self.fps_fixed = True
            else:
                limits = self._frame_duration_limits(fps, fixed=False)
                self.picam2.set_controls({"FrameDurationLimits": limits})
        full = self.picam2.camera_configuration()
        self.main_config = dict(full["main"])
        self.lores_config = dict(full.get("lores") or {})
        # The main/raw formats are ISP/PiSP internal (XBGR8888 / *_PISP_COMP*) and
        # do not match what rpicam-hello --list-cameras reports, so resolve the
        # actual sensor mode libcamera selected for the GUI to display.
        self.sensor_config = dict(full.get("sensor") or {})
        self.sensor_mode = self._match_sensor_mode(self.sensor_config)
        self.current_mode = mode
        self.fps_current = float(fps)
        self.size = tuple(self.lores_config.get("size", lores_size))
        # configure() resets picam2.controls to the config's, so re-clamp
        # manual values against the new mode and push them again.
        self._clamp_control_state()
        self._apply_controls()
        log.info("configured: sensor_mode=%s fps=%.2f main=%s lores=%s",
                 self.sensor_mode_str(), fps,
                 self.main_config.get("size"), self.size)

    def apply_mode(self, mode: SensorMode, fps: float, avail_size,
                   fps_fixed: bool = True) -> None:
        """Reconfigure to a new mode/fps while running (stop, configure, start)."""
        was_started = self._started
        if was_started:
            self.stop()
        self.configure_mode(mode, fps, avail_size, fps_fixed)
        if was_started:
            self.start()

    def _sensor_max_frame_us(self) -> int | None:
        """Sensor's advertised max frame duration, None if missing or malformed."""
        limits = self._camera_controls.get("FrameDurationLimits")
        if not isinstance(limits, (tuple, list)) or len(limits) < 2:
            return None
        try:
            return int(limits[1])
        except (TypeError, ValueError, OverflowError):
            return None

    def _frame_duration_limits(self, fps: float, fixed: bool) -> tuple[int, int]:
        """(min, max) FrameDurationLimits for the FPS lock policy.

        Fixed pins both ends, exposure driven widens the max toward 1 s.
        Raises ValueError on an over-cap fps duration or missing sensor limit.
        """
        dur = fps_to_frame_duration(fps)
        if dur > _MAX_FRAME_US:
            raise ValueError(f"frame duration {dur} exceeds {_MAX_FRAME_US}")
        if fixed:
            return dur, dur
        sensor_hi = self._sensor_max_frame_us()
        if sensor_hi is None:
            raise ValueError("sensor advertises no usable FrameDurationLimits")
        hi = min(_MAX_FRAME_US, sensor_hi)
        if dur > hi:
            raise ValueError(f"frame duration {dur} exceeds sensor limit {hi}")
        return dur, hi

    # camera controls (exposure / gain / white balance)
    @property
    def _camera_controls(self) -> dict:
        """picam2.camera_controls, cached per configure. Empty without a camera.

        Each picamera2 access rebuilds the whole dict and a slider drag
        reads it several times per tick.
        """
        if self.picam2 is None:
            return {}
        if self._cc_cache is None:
            self._cc_cache = self.picam2.camera_controls
        return self._cc_cache

    def control_ranges(self) -> dict[str, tuple]:
        """(min, max) per manual control for the current configuration.

        Keys mirror ControlState fields. A missing key means the camera does
        not offer that control (e.g. no ColourTemperature on mono sensors), so
        GUI hides it. Ranges come from camera_controls, except exposure max,
        capped at one frame duration of the locked fps (the 1 s ceiling when
        FPS is exposure driven).
        """
        if self.picam2 is None:
            return {}
        cc = self._camera_controls
        ranges: dict[str, tuple] = {}
        if "ExposureTime" in cc:
            lo, hi, _ = cc["ExposureTime"]
            if self.fps_current:
                _, cap = self._frame_duration_limits(self.fps_current,
                                                     self.fps_fixed)
                hi = min(hi, cap)
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
        On a slow pipeline a coalesced flush is armed, so the change reaches
        the sensor ahead of queued long-exposure frames.
        """
        # Sample before applying: slowness must reflect the requests already
        # queued, not the control being set now.
        flush_worthwhile = self._slow_pipeline
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
        if flush_worthwhile:
            self._flush_pending = True
            self._schedule_flush(0)
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
        if "StatsOutputEnable" in self._camera_controls:
            ctrls["StatsOutputEnable"] = self.stats_output
        if ctrls:
            self.picam2.set_controls(ctrls)

    # pipeline flush (coalesced restart for slow pipelines)
    @property
    def _flush_ready(self) -> bool:
        """True once a frame arrived since the last start (or 10 s timeout).

        Gates back-to-back flushes. The timeout only unjams a dead pipeline.
        """
        if self._frame_since_start:
            return True
        return time.monotonic() - self._start_ts > 10.0

    @property
    def _slow_pipeline(self) -> bool:
        """True when frame duration makes queued controls visibly laggy.

        Checks programmed exposure besides live metadata, which covers a
        fresh start where a long manual exposure is set but no frame has
        arrived yet.
        """
        if not self._started or self.fps_fixed:
            return False
        dur = self.telemetry.metadata.get("FrameDuration") or 0
        exp = self.control_state.exposure_us or 0
        return max(dur, exp) > _SLOW_FRAME_US

    def _schedule_flush(self, delay_ms: int) -> None:
        """(Re)arm the flush drain on the event loop.

        One single-shot timer coalesces all triggers: control changes,
        frame arrivals and the not-ready retry.
        """
        if self._flush_timer is None:
            self._flush_timer = QtCore.QTimer()
            self._flush_timer.setSingleShot(True)
            self._flush_timer.timeout.connect(self._drain_flush)
        self._flush_timer.start(delay_ms)

    def _drain_flush(self) -> None:
        if not self._flush_pending:
            return
        if not self._flush_ready:
            # No frame since the last restart. The next frame re-arms at 0,
            # this retry only covers a pipeline that stopped delivering.
            self._schedule_flush(500)
            return
        self._flush_pending = False
        try:
            self._flush_controls()
        except Exception:
            log.exception("pipeline flush failed")

    def _flush_controls(self) -> None:
        """Restart capture so current controls reach the sensor now.

        Drops the in-flight request queue. Telemetry, histogram and frame
        counter carry across the restart.
        """
        if not self._started:
            return
        st = self.control_state
        log.debug("flush: restart with exposure=%s gain=%s", st.exposure_us,
                  st.gain)
        self.stop()
        # Re-apply after stop: picamera2 controls are a pending delta wiped
        # by start, and queued requests that held them are gone.
        self._apply_controls()
        self.start(reset_telemetry=False)

    def set_stats_output(self, enabled: bool) -> None:
        """Deliver ISP statistics (PispStatsOutput) with each frame's metadata.

        Feeds the histogram overlay. Off by default: with stats on, the
        binding converts the 23 kB blob on every frame that carries it.
        """
        self.stats_output = bool(enabled)
        if not self.stats_output:
            self.latest_histogram = None
        if (self.picam2 is not None
                and "StatsOutputEnable" in self._camera_controls):
            self.picam2.set_controls({"StatsOutputEnable": self.stats_output})

    @staticmethod
    def agc_histogram(metadata: dict) -> np.ndarray | None:
        """1024-bin luma histogram from the ISP frontend stats, else None.

        The PiSP frontend counts these bins for AGC on every frame, so the
        histogram costs no per-pixel CPU work. Above ~30 fps libcamera skips
        the blob on some frames (callers keep the previous histogram).
        """
        blob = metadata.get("PispStatsOutput")
        if not blob:
            return None
        raw = bytes(blob)
        if len(raw) < _AGC_HIST_OFFSET + _AGC_HIST_BINS * 4:
            return None
        return np.frombuffer(raw, dtype=np.uint32, count=_AGC_HIST_BINS,
                             offset=_AGC_HIST_OFFSET)

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
        # Picamera2 calls this from Qt's event loop. Sensor timestamps yield fps.
        # Sequence offset preserves frame numbering across flushes.
        prev = self.telemetry
        lib_req = getattr(request, "request", None)
        frame = (lib_req.sequence + self._seq_base
                 if lib_req is not None else prev.frame)
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
        # Publish the frame's readout as one snapshot so readers get a
        # consistent set.
        self.telemetry = Telemetry(frame=frame, fps=fps, metadata=md)
        if not self._frame_since_start:
            self._frame_since_start = True
            log.debug("first frame %.2f s after start (exp=%s)",
                      time.monotonic() - self._start_ts,
                      md.get("ExposureTime"))
        if self._flush_pending:
            # Runs inside request processing where a restart is off limits,
            # so drain on the event loop.
            self._schedule_flush(0)
        # Latch the histogram off any frame carrying stats (~30 Hz ceiling),
        # so the GUI's sampling never lands on a blob-less frame.
        if self.stats_output:
            hist = self.agc_histogram(md)
            if hist is not None:
                self.latest_histogram = hist
        if not self._first_frame_seen:
            self._first_frame_seen = True
            boot_time = time.clock_gettime(time.CLOCK_BOOTTIME)
            if self._first_frame_cb:
                try:
                    self._first_frame_cb(boot_time)
                except Exception:  # never let UI timing break capture
                    log.exception("first-frame callback failed")

    def start(self, *, reset_telemetry: bool = True) -> None:
        """Start capture. reset_telemetry=False is for the mid-run flush
        restart: keep the snapshot and continue the frame numbering."""
        if self.picam2 is None:
            raise RuntimeError("camera not opened")
        if self.current_mode is None:
            raise RuntimeError("camera not configured (call configure_mode first)")
        if reset_telemetry:
            # Fresh run: clear the last snapshot so a mode switch reads as a
            # new capture.
            self.telemetry = Telemetry()
            self.latest_histogram = None
            self._seq_base = 0
        else:
            # libcamera restarts the request sequence at 0, offset it so the
            # frame counter continues from the last snapshot.
            self._seq_base = (self.telemetry.frame + 1
                              if self.telemetry.frame is not None else 0)
        self._last_ts = 0
        self._frame_since_start = False
        self._start_ts = time.monotonic()
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
