# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Sensor mode catalogue and selection (pure, no Picamera2/Qt).

A mode is one raw sensor output: packed format, size, bit depth, max fps.
Operator picks via Resolution --> Bit depth --> FPS. Bench rates (24, 30, 60,
120) capped by mode and MAX_FPS, plus sensor max when it sits between rates.
Display never limits sensor rate. Default without a persisted pick: heaviest
mode at DEFAULT_FPS.
"""

from __future__ import annotations

from dataclasses import dataclass

# Standard bench rates, lowest first. Sensor caps surface alongside these.
BASE_FPS: tuple[float, ...] = (24.0, 30.0, 60.0, 120.0)

# App ceiling. Higher rates run but start unreliably (AR0234 960x600 claims
# 236.85, locks about half the time).
MAX_FPS = 120.0

# Boot rate when nothing is persisted. Higher rates are opt-in.
DEFAULT_FPS = 30.0

# Tolerance when matching reported fps (e.g. 33.89) to nominal rates.
_FPS_EPS = 0.5

# Lores alignment. Even size avoids fractional scaling artefacts.
_LORES_ALIGN = 2


@dataclass(frozen=True)
class SensorMode:
    """One raw mode the sensor can deliver."""

    format: str  # libcamera packed name, e.g. "SGRBG12_CSI2P"
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
    """De-duplicated mode list from Picamera2.sensor_modes.

    raw_modes is the list of dicts picamera2 exposes (format, bit_depth, size,
    fps). Keyed by (size, bit_depth), so duplicates collapse. Sorted heaviest
    last (area, then bit depth, then fps).
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
        # Keep higher fps when the stack lists the same mode twice.
        if prev is None or sm.max_fps > prev.max_fps:
            by_key[(size, depth)] = sm
    return sorted(by_key.values(), key=lambda s: (s.area, s.bit_depth, s.max_fps))


def fps_options(max_fps: float) -> list[float]:
    """FPS choices for a mode under bench policy.

    eff = min(sensor max, MAX_FPS). At or below 24: one locked option. Above:
    standard rates that fit, plus eff when it sits between two rates
    (33.89 --> [24, 30, 33.89]). One element means lock the selector.
    """
    eff = min(max_fps, MAX_FPS)
    if eff <= BASE_FPS[0] + _FPS_EPS:
        return [BASE_FPS[0]] if eff >= BASE_FPS[0] - _FPS_EPS else [round(eff, 2)]
    opts = [r for r in BASE_FPS if r <= eff + _FPS_EPS]
    if eff - opts[-1] > _FPS_EPS:
        opts.append(round(eff, 2))
    return opts


def format_fps(fps: float) -> str:
    """Human fps: '30', '60', '33.89'. Whole numbers drop the decimals."""
    return str(round(fps)) if abs(fps - round(fps)) < 1e-6 else f"{fps:.2f}"


def fps_to_frame_duration(fps: float) -> int:
    """Frame duration in microseconds for a target fps (for FrameDurationLimits)."""
    return round(1_000_000.0 / fps)


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


def mode_for(modes: list[SensorMode], size: tuple[int, int], bit_depth: int) -> SensorMode | None:
    """The mode with this exact size + bit depth, if any."""
    for m in modes:
        if m.size == size and m.bit_depth == bit_depth:
            return m
    return None


def default_mode(modes: list[SensorMode]) -> tuple[SensorMode, float]:
    """Heaviest mode (largest area, deepest bits) at DEFAULT_FPS.

    No per-sensor defaults are predefined: the heaviest runnable mode is the
    default whenever there is no (valid) persisted selection. Its rate is
    DEFAULT_FPS, or the nearest offered rate when the mode cannot do it.
    """
    if not modes:
        raise ValueError("no sensor modes to choose from")
    best = max(modes, key=lambda m: (m.area, m.bit_depth, m.max_fps))
    return best, nearest_fps_option(fps_options(best.max_fps), DEFAULT_FPS)


def resolve_initial_mode(modes: list[SensorMode], saved: dict | None) -> tuple[SensorMode, float]:
    """Pick the boot mode: a valid persisted selection, else the heaviest mode.

    A persisted selection is honoured only if its (size, bit_depth) still exists.
    Its fps snaps to the nearest offered rate when no longer offered (no stale,
    unrunnable rates), same as a runtime mode change. Missing fps means no
    intent to preserve, so DEFAULT_FPS applies.
    """
    if saved:
        size = saved.get("size")
        size = tuple(size) if size else None
        depth = saved.get("bit_depth")
        if size is not None and depth is not None:
            m = mode_for(modes, (int(size[0]), int(size[1])), int(depth))
            if m is not None:
                fps = saved.get("fps")
                return m, nearest_fps_option(
                    fps_options(m.max_fps), DEFAULT_FPS if fps is None else fps
                )
    return default_mode(modes)


def plan_lores_size(main_size: tuple[int, int], avail_size: tuple[int, int]) -> tuple[int, int]:
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
