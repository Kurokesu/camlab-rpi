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
def logo() -> np.ndarray:
    """Wordmark shaped like the shipped logo.tga."""
    return np.zeros((120, 600, 3), np.uint8)


@pytest.fixture
def canvas() -> np.ndarray:
    return np.zeros((600, 800, 3), np.uint8)


def fill_pixels(fbsplash, canvas: np.ndarray, box, fraction: float) -> int:
    canvas[:] = 0
    fbsplash.draw_bar(canvas, box, fraction)
    return int(np.all(canvas == fbsplash.INK, axis=2).sum())


def test_fill_follows_the_fraction(fbsplash, canvas, logo):
    box = fbsplash.place(logo, 800, 600)
    assert fill_pixels(fbsplash, canvas, box, 0.0) == 0
    half = fill_pixels(fbsplash, canvas, box, 0.5)
    full = fill_pixels(fbsplash, canvas, box, 1.0)
    assert half == pytest.approx(full / 2, rel=0.05)


def test_empty_bar_still_shows_its_track(fbsplash, canvas, logo):
    fbsplash.draw_bar(canvas, fbsplash.place(logo, 800, 600), 0.0)
    assert np.any(np.all(canvas == fbsplash.TRACK, axis=2))


def test_fraction_out_of_range_is_clamped(fbsplash, canvas, logo):
    box = fbsplash.place(logo, 800, 600)
    assert fill_pixels(fbsplash, canvas, box, 2.0) == fill_pixels(fbsplash, canvas, box, 1.0)
    assert fill_pixels(fbsplash, canvas, box, -1.0) == fill_pixels(fbsplash, canvas, box, 0.0)


def test_bar_stays_on_a_short_framebuffer(fbsplash, logo):
    """A rotated panel leaves few rows, the bar must still land inside them."""
    canvas = np.zeros((32, 64, 3), np.uint8)
    fbsplash.draw_bar(canvas, fbsplash.place(logo, 64, 32), 1.0)
    assert np.any(np.all(canvas == fbsplash.INK, axis=2))


def test_label_sits_under_the_bar(fbsplash, canvas, logo):
    box = fbsplash.place(logo, 800, 600)
    bottom = fbsplash.draw_bar(canvas, box, 0.5)
    fbsplash.draw_label(canvas, box, bottom, "Installing updates")
    assert np.any(np.all(canvas[bottom:] == fbsplash.INK, axis=2))
