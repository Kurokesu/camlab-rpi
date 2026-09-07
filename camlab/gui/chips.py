# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Camera control chips: spec table and value text."""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple


def fmt_exposure(us: float) -> str:
    if us >= 1_000_000:
        return f"{us / 1_000_000:.2f} s"
    if us >= 10000:
        return f"{us / 1000:.1f} ms"
    if us >= 1000:
        return f"{us / 1000:.2f} ms"
    return f"{round(us)} \u00b5s"


def fmt_gain(gain: float) -> str:
    return f"{gain:.2f}x"


def fmt_ct(kelvin: float) -> str:
    return f"{round(kelvin)} K"


class ChipSpec(NamedTuple):
    """One camera-control chip: label, icon, metadata source, formatting."""

    label: str
    glyph: str
    md_key: str
    fmt: Callable[[float], str]
    sample: str  # widest realistic value, pins chip width


CTRL_SPEC: dict[str, ChipSpec] = {
    "exposure_us": ChipSpec("Exp", "shutter_speed", "ExposureTime", fmt_exposure, "888.8 ms"),
    "gain": ChipSpec("Gain", "iso", "AnalogueGain", fmt_gain, "88.88x"),
    "colour_temp": ChipSpec("WB", "wb_sunny", "ColourTemperature", fmt_ct, "8888 K"),
}


def _labelled(spec: ChipSpec, body: str, compact: bool) -> str:
    return f" {body}" if compact else f" {spec.label} {body}"


def chip_text(spec: ChipSpec, value: float | None, compact: bool) -> str:
    """Value text, "--" until metadata. Compact drops label."""
    return _labelled(spec, spec.fmt(value) if value is not None else "--", compact)


def chip_sample(spec: ChipSpec, compact: bool) -> str:
    return _labelled(spec, spec.sample, compact)
