# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Progress bar geometry, the only arithmetic in the boot painter."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

SOURCE = Path(__file__).parent.parent / "deploy" / "splash" / "fbsplash.py"


@pytest.fixture(scope="module")
def fbsplash():
    """deploy/ ships as plain scripts, so load it by path."""
    spec = importlib.util.spec_from_file_location("fbsplash", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def canvas() -> np.ndarray:
    return np.zeros((600, 800, 3), np.uint8)


def bar_pixels(fbsplash, canvas: np.ndarray, fraction: float) -> int:
    canvas[:] = 0
    fbsplash.draw_bar(canvas, fraction)
    return int(np.all(canvas == fbsplash.BAR_COLOR, axis=2).sum())


def test_fill_follows_the_fraction(fbsplash, canvas):
    outline = bar_pixels(fbsplash, canvas, 0.0)
    half = bar_pixels(fbsplash, canvas, 0.5)
    full = bar_pixels(fbsplash, canvas, 1.0)
    assert outline < half < full
    assert half - outline == pytest.approx((full - outline) / 2, rel=0.05)


def test_empty_bar_still_shows_its_outline(fbsplash, canvas):
    assert bar_pixels(fbsplash, canvas, 0.0) > 0


def test_fraction_out_of_range_is_clamped(fbsplash, canvas):
    assert bar_pixels(fbsplash, canvas, 2.0) == bar_pixels(fbsplash, canvas, 1.0)
    assert bar_pixels(fbsplash, canvas, -1.0) == bar_pixels(fbsplash, canvas, 0.0)


def test_bar_stays_on_a_short_framebuffer(fbsplash):
    """A rotated panel leaves few rows, the bar must still land inside them."""
    canvas = np.zeros((32, 64, 3), np.uint8)
    fbsplash.draw_bar(canvas, 1.0)
    assert np.any(np.all(canvas == fbsplash.BAR_COLOR, axis=2))
