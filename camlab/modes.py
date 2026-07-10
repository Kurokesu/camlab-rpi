"""Sensor mode catalogue + selection logic (pure, no Picamera2/Qt imports).

A "mode" is one raw output the sensor can deliver: a libcamera packed format
(e.g. SGRBG12_CSI2P), an output size, a bit depth and the sensor's max fps for
that combination. The GUI lets the operator pick one at runtime as a cascade:

    Resolution  ->  Bit depth  ->  FPS

FPS choices follow a fixed policy (see fps_options): the standard bench rates
30 and 60, capped by what the sensor mode and the display can actually sustain,
with the sensor's own maximum surfaced as the top option when it falls between
two standard rates. The "max stress" default picks the heaviest mode the rig can
run (largest area, deepest bits, highest fps within the display limit).

Everything here is deterministic and side-effect free so it can be unit tested
and reasoned about without a camera. CameraEngine turns a (SensorMode, fps) pair
into an actual Picamera2 configuration.
"""

from __future__ import annotations

from dataclasses import dataclass

# Standard bench framerates, lowest first. Anything else offered is a sensor- or
# display-imposed cap surfaced alongside these.
BASE_FPS: tuple[float, ...] = (30.0, 60.0)

# Default display refresh ceiling when we cannot read it from the screen. Most
# bench HDMI panels are 60 Hz, which is also the policy ceiling for "stress".
DEFAULT_DISPLAY_MAX_FPS = 60.0

# Tolerance when comparing reported fps (floats like 33.89) to nominal rates.
_FPS_EPS = 0.5

# Lores stream alignment. Picamera2 aligns further, but keeping our planned size
# even avoids fractional scaling artefacts.
_LORES_ALIGN = 2


@dataclass(frozen=True)
class SensorMode:
    """One raw mode the sensor can deliver."""

    format: str               # libcamera packed name, e.g. "SGRBG12_CSI2P"
    size: tuple[int, int]
    bit_depth: int
    max_fps: float

    @property
    def area(self) -> int:
        return self.size[0] * self.size[1]

    @property
    def width(self) -> int:
        return self.size[0]

    @property
    def height(self) -> int:
        return self.size[1]

    def label(self) -> str:
        return f"{self.format} {self.size[0]}x{self.size[1]}"


def enumerate_modes(raw_modes) -> list[SensorMode]:
    """Build a clean, de-duplicated mode list from Picamera2.sensor_modes.

    raw_modes is the list of dicts picamera2 exposes (keys: format, bit_depth,
    size, fps, ...). Modes are keyed by (size, bit_depth), so duplicates collapse.
    Sorted heaviest last (area, then bit depth, then fps) for stable iteration.
    """
    by_key: dict[tuple[tuple[int, int], int], SensorMode] = {}
    for m in raw_modes:
        size = tuple(m.get("size") or ())
        if len(size) != 2:
            continue
        size = (int(size[0]), int(size[1]))
        depth = int(m.get("bit_depth") or 0)
        fps = float(m.get("fps") or 0.0)
        fmt = str(m.get("format") or "")
        sm = SensorMode(format=fmt, size=size, bit_depth=depth, max_fps=fps)
        prev = by_key.get((size, depth))
        # Keep the higher fps if the stack lists the same mode twice.
        if prev is None or sm.max_fps > prev.max_fps:
            by_key[(size, depth)] = sm
    return sorted(by_key.values(), key=lambda s: (s.area, s.bit_depth, s.max_fps))


def fps_options(max_fps: float,
                display_max_fps: float = DEFAULT_DISPLAY_MAX_FPS) -> list[float]:
    """FPS choices for a mode, honouring the bench policy.

    eff = min(sensor max, display max). Then:
      - eff <= 30  -> a single locked option (30 if it lands on 30, else eff).
      - eff > 30   -> the standard rates (30, 60) that fit under eff, plus eff
                      itself when it sits strictly between two standard rates
                      (e.g. 33.89 -> [30, 33.89], 40.03 -> [30, 40.03]).
    A single-element result means the selector should be locked (unselectable).
    """
    eff = min(max_fps, display_max_fps)
    if eff <= BASE_FPS[0] + _FPS_EPS:
        return [BASE_FPS[0]] if eff >= BASE_FPS[0] - _FPS_EPS else [round(eff, 2)]
    opts = [r for r in BASE_FPS if r <= eff + _FPS_EPS]
    if eff - opts[-1] > _FPS_EPS:
        opts.append(round(eff, 2))
    return opts


def format_fps(fps: float) -> str:
    """Human fps: '30', '60', '33.89'. Whole numbers drop the decimals."""
    return str(int(round(fps))) if abs(fps - round(fps)) < 1e-6 else f"{fps:.2f}"


def fps_to_frame_duration(fps: float) -> int:
    """Frame duration in microseconds for a target fps (for FrameDurationLimits)."""
    return int(round(1_000_000.0 / fps))


def match_fps_option(options: list[float], fps: float | None) -> float | None:
    """Return the option matching fps within tolerance, else None."""
    if fps is None:
        return None
    for o in options:
        if abs(o - fps) <= _FPS_EPS:
            return o
    return None


def nearest_fps_option(options: list[float], target: float | None) -> float:
    """Option closest to target (ties favour the lower rate).

    target None means "no preference" and returns the maximum available rate.
    Used to carry the chosen fps across a mode change: kept when still offered,
    otherwise the nearest achievable rate (e.g. 60 -> 33.89 when 60 drops out).
    """
    if target is None:
        return options[-1]
    return min(options, key=lambda o: (abs(o - target), o))


def resolutions(modes: list[SensorMode]) -> list[tuple[int, int]]:
    """Distinct output sizes, largest (heaviest) first."""
    seen: dict[tuple[int, int], int] = {}
    for m in modes:
        seen.setdefault(m.size, m.area)
    return sorted(seen, key=lambda s: seen[s], reverse=True)


def bit_depths_for(modes: list[SensorMode], size: tuple[int, int]) -> list[int]:
    """Distinct bit depths available at a size, deepest first."""
    depths = {m.bit_depth for m in modes if m.size == size}
    return sorted(depths, reverse=True)


def mode_for(modes: list[SensorMode], size: tuple[int, int],
             bit_depth: int) -> SensorMode | None:
    """The mode with this exact size + bit depth, if any."""
    for m in modes:
        if m.size == size and m.bit_depth == bit_depth:
            return m
    return None


def default_mode(modes: list[SensorMode],
                 display_max_fps: float = DEFAULT_DISPLAY_MAX_FPS
                 ) -> tuple[SensorMode, float]:
    """Max-stress default: largest area, then deepest bits, then highest fps.

    No per-sensor defaults are predefined: the heaviest runnable mode is the
    stress baseline whenever there is no (valid) persisted selection.
    """
    if not modes:
        raise ValueError("no sensor modes to choose from")
    best = max(modes, key=lambda m: (m.area, m.bit_depth, m.max_fps))
    return best, fps_options(best.max_fps, display_max_fps)[-1]


def resolve_initial_mode(modes: list[SensorMode], saved: dict | None,
                         display_max_fps: float = DEFAULT_DISPLAY_MAX_FPS
                         ) -> tuple[SensorMode, float]:
    """Pick the boot mode: a valid persisted selection, else the stress default.

    A persisted selection is honoured only if its (size, bit_depth) still exists.
    Its fps is used when it is still a valid option, otherwise we snap to the max
    available fps for that mode (no stale, unrunnable rates).
    """
    if saved:
        size = saved.get("size")
        size = tuple(size) if size else None
        depth = saved.get("bit_depth")
        if size is not None and depth is not None:
            m = mode_for(modes, (int(size[0]), int(size[1])), int(depth))
            if m is not None:
                opts = fps_options(m.max_fps, display_max_fps)
                chosen = match_fps_option(opts, saved.get("fps"))
                return m, (chosen if chosen is not None else opts[-1])
    return default_mode(modes, display_max_fps)


def plan_lores_size(main_size: tuple[int, int],
                    avail_size: tuple[int, int]) -> tuple[int, int]:
    """Largest lores size with main aspect ratio that fits viewfinder area.

    Lores stream is what the GL widget shows. We keep it at the main aspect
    ratio (so the ISP scale is undistorted) and never upscale beyond main.
    """
    mw, mh = main_size
    aw, ah = avail_size
    if aw <= 0 or ah <= 0:
        aw, ah = 1280, 720
    scale = min(aw / mw, ah / mh, 1.0)
    lw = max(_LORES_ALIGN, int(mw * scale))
    lh = max(_LORES_ALIGN, int(mh * scale))
    lw -= lw % _LORES_ALIGN
    lh -= lh % _LORES_ALIGN
    return (min(lw, mw), min(lh, mh))
