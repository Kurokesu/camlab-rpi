# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""FPS choices per mode and preview stream sizing: main bound, lores fit and area budget."""

from __future__ import annotations

import pytest

from camlab.modes import (
    _STREAM_MAX_PIXELS,
    SensorMode,
    fps_options,
    plan_lores_size,
    plan_main_size,
    resolve_initial_mode,
)

# Viewfinder area on each head, screen minus chrome
AVAIL_1080P = (1920, 1006)
AVAIL_1440P = (2560, 1366)

SENSORS = {
    "ar0822 4K": (3840, 2160),
    "ar0822 1080p": (1920, 1080),
    "ar0234": (1920, 1200),
    "imx585": (3856, 2180),
}

# Sensor to lores the 1080p head must keep producing
LORES_1080P = {
    "ar0822 4K": ((3840, 2160), (1788, 1006)),
    "ar0822 1080p": ((1920, 1080), (1788, 1006)),
    "ar0234": ((1920, 1200), (1608, 1006)),
    "imx585": ((3856, 2180), (1780, 1006)),
}


def _mode(size: tuple[int, int], max_fps: float, bit_depth: int = 12) -> SensorMode:
    return SensorMode(format="", size=size, bit_depth=bit_depth, max_fps=max_fps)


def test_isp_rate_caps_4k_without_offering_ceiling():
    """4K at 60 overruns ISP front end, ceiling is a bound rather than a rate."""
    assert fps_options(_mode((3840, 2160), 60.01, bit_depth=10)) == [24.0, 30.0]


def test_sensor_max_between_rates_is_offered():
    assert fps_options(_mode((3840, 2160), 33.89)) == [24.0, 30.0, 33.89]


def test_app_ceiling_holds_small_mode():
    assert fps_options(_mode((960, 600), 236.85, bit_depth=10)) == [24.0, 30.0, 60.0, 120.0]


def test_isp_ceiling_below_every_standard_rate_is_sole_option():
    assert fps_options(_mode((5472, 3648), 25.0)) == [19.04]


def test_saved_rate_over_isp_ceiling_snaps_down():
    modes = [_mode((3840, 2160), 60.01, bit_depth=10)]
    saved = {"size": [3840, 2160], "bit_depth": 10, "fps": 60.0}
    assert resolve_initial_mode(modes, saved) == (modes[0], 30.0)


@pytest.mark.parametrize("sensor", SENSORS.values(), ids=list(SENSORS))
def test_main_within_budget(sensor):
    w, h = plan_main_size(sensor)
    assert w * h <= _STREAM_MAX_PIXELS
    assert (w, h) <= sensor


def test_main_leaves_small_sensor_alone():
    assert plan_main_size((1920, 1080)) == (1920, 1080)


def test_main_keeps_aspect_within_pixel():
    w, h = plan_main_size((3840, 2160))
    assert abs(w / h - 3840 / 2160) < 0.01


@pytest.mark.parametrize("sensor", SENSORS.values(), ids=list(SENSORS))
def test_lores_stays_within_main(sensor):
    main = plan_main_size(sensor)
    for avail in (AVAIL_1080P, AVAIL_1440P):
        lores = plan_lores_size(main, avail)
        assert lores <= main


@pytest.mark.parametrize(("sensor", "expected"), LORES_1080P.values(), ids=list(LORES_1080P))
def test_budget_leaves_1080p_head_alone(sensor, expected):
    assert plan_lores_size(plan_main_size(sensor), AVAIL_1080P) == expected


def test_budget_binds_on_larger_head():
    lores = plan_lores_size(plan_main_size((3840, 2160)), AVAIL_1440P)
    assert lores == (1920, 1080)


def test_sizes_stay_even():
    for avail in (AVAIL_1080P, AVAIL_1440P, (1281, 721)):
        w, h = plan_lores_size(plan_main_size((3856, 2180)), avail)
        assert w % 2 == 0 and h % 2 == 0


def test_degenerate_avail_falls_back():
    assert plan_lores_size((3840, 2160), (0, 0)) == plan_lores_size((3840, 2160), (1280, 720))
