# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Grey world AWB: trimmed estimator, gain easing and the AwbEnable handover."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("picamera2")

from camlab import camera
from camlab.camera import CameraEngine, grey_world_gains

PIXELS = 1000
GREY = 4000  # 16-bit channel mean well above the lit threshold

COLOUR_CONTROLS = {
    "ColourTemperature": (100, 100000, 4500),
    "AwbEnable": (False, True, True),
    "StatsOutputEnable": (False, True, False),
}


def zones(count: int, r: float, g: float, b: float, pixels: int = PIXELS) -> np.ndarray:
    """count zones of the same colour, per-channel means times pixel count."""
    row = np.array([r * pixels, g * pixels, b * pixels, pixels], dtype=np.uint32)
    return np.tile(row, (count, 1))


def blob(*parts: np.ndarray) -> dict:
    """Metadata carrying a stats blob whose AWB grid is these zones."""
    grid = np.zeros((camera._AWB_ZONES, 4), dtype=np.uint32)
    stacked = np.vstack(parts)
    grid[: len(stacked)] = stacked
    return {"PispStatsOutput": grid.tobytes()}


class FakePicam2:
    def __init__(self, controls: dict):
        self.camera_controls = controls
        self.pushed: list[dict] = []

    def set_controls(self, ctrls: dict) -> None:
        self.pushed.append(dict(ctrls))


def _engine(controls: dict = COLOUR_CONTROLS) -> tuple[CameraEngine, FakePicam2]:
    engine = CameraEngine()
    engine.picam2 = FakePicam2(controls)
    return engine, engine.picam2


def _gains(picam2: FakePicam2) -> list[tuple[float, float]]:
    return [c["ColourGains"] for c in picam2.pushed if "ColourGains" in c]


def _frame(engine: CameraEngine, metadata: dict) -> None:
    """Deliver one frame the way picamera2 does, through the pre-callback."""
    engine._pre_callback(SimpleNamespace(request=None, get_metadata=lambda: metadata))


class TestEstimator:
    def test_grey_scene_gives_unit_gains(self):
        assert grey_world_gains(zones(64, GREY, GREY, GREY)) == pytest.approx((1.0, 1.0), abs=1e-3)

    def test_tinted_scene_equalises_channels(self):
        got = grey_world_gains(zones(64, GREY / 2, GREY, GREY / 3))
        assert got == pytest.approx((2.0, 3.0), abs=1e-2)

    def test_outer_quarters_drop_a_coloured_object(self):
        red_object = zones(20, GREY * 3, GREY, GREY)
        got = grey_world_gains(np.vstack([zones(80, GREY, GREY, GREY), red_object]))
        assert got == pytest.approx((1.0, 1.0), abs=1e-3)

    def test_outliers_beyond_the_trim_leak_in(self):
        red_object = zones(40, GREY * 3, GREY, GREY)
        got = grey_world_gains(np.vstack([zones(60, GREY, GREY, GREY), red_object]))
        assert got[0] < 0.9

    def test_sparse_and_dark_zones_are_skipped(self):
        sparse = zones(30, GREY * 8, GREY, GREY, pixels=camera._AWB_ZONE_MIN_PIXELS - 1)
        dark = zones(30, GREY, camera._AWB_ZONE_MIN_G - 1, GREY / 8)
        got = grey_world_gains(np.vstack([zones(40, GREY, GREY, GREY), sparse, dark]))
        assert got == pytest.approx((1.0, 1.0), abs=1e-3)

    def test_too_few_lit_zones_gives_none(self):
        assert grey_world_gains(zones(camera._AWB_MIN_ZONES, GREY, GREY, GREY)) is None
        assert grey_world_gains(zones(camera._AWB_MIN_ZONES + 1, GREY, GREY, GREY)) is not None

    def test_gains_clamp_to_the_practical_range(self):
        lo, hi = camera._WB_GAIN_RANGE
        assert grey_world_gains(zones(64, GREY / 20, GREY, GREY * 20)) == (hi, lo)


class TestEngine:
    def test_awb_zones_read_the_head_of_the_blob(self):
        got = CameraEngine.awb_zones(blob(zones(3, 1, 2, 3, pixels=4)))
        assert got.shape == (camera._AWB_ZONES, 4)
        assert got[2].tolist() == [4, 8, 12, 4]
        assert CameraEngine.awb_zones({}) is None
        assert CameraEngine.awb_zones({"PispStatsOutput": b"\0" * 16}) is None

    def test_grey_world_freezes_libcamera_awb_and_takes_stats(self):
        engine, picam2 = _engine()
        engine.set_grey_world(True)
        assert picam2.pushed[-1]["AwbEnable"] is False
        assert "ColourTemperature" not in picam2.pushed[-1]
        assert engine.stats_output

    def test_libcamera_awb_is_the_default_and_releases_stats(self):
        engine, picam2 = _engine()
        engine._apply_controls()
        assert picam2.pushed[-1]["AwbEnable"] is True
        engine.set_grey_world(True)
        engine.set_grey_world(False)
        assert picam2.pushed[-1]["AwbEnable"] is True
        assert not engine.stats_output

    def test_manual_kelvin_wins_over_grey_world(self):
        engine, picam2 = _engine()
        engine.set_grey_world(True)
        engine.set_control_state(colour_temp=5000)
        assert picam2.pushed[-1]["AwbEnable"] is False
        assert picam2.pushed[-1]["ColourTemperature"] == 5000
        assert not engine.stats_output
        engine.set_control_state(colour_temp=None)
        assert engine.stats_output
        assert "ColourTemperature" not in picam2.pushed[-1]

    def test_mono_sensor_is_a_noop(self):
        engine, picam2 = _engine({"StatsOutputEnable": (False, True, False)})
        engine.set_grey_world(True)
        assert "AwbEnable" not in picam2.pushed[-1]
        assert not engine.stats_output
        _frame(engine, blob(zones(64, GREY, GREY, GREY)))
        assert _gains(picam2) == []

    def test_first_sample_jumps_then_eases(self):
        engine, picam2 = _engine()
        engine.set_grey_world(True)
        _frame(engine, blob(zones(64, GREY / 2, GREY, GREY / 2)))
        _frame(engine, blob(zones(64, GREY, GREY, GREY)))
        first, second = _gains(picam2)
        assert first == pytest.approx((2.0, 2.0), abs=1e-3)
        expected = 2.0 - (2.0 - 1.0) * camera._WB_SPEED
        assert second == pytest.approx((expected, expected), abs=1e-3)

    def test_settled_gains_stop_pushing(self):
        engine, picam2 = _engine()
        engine.set_grey_world(True)
        for _ in range(5):
            _frame(engine, blob(zones(64, GREY, GREY, GREY)))
        assert len(_gains(picam2)) == 1

    def test_blobless_frame_is_skipped(self):
        engine, picam2 = _engine()
        engine.set_grey_world(True)
        _frame(engine, {})
        _frame(engine, blob(zones(3, GREY, GREY, GREY)))
        assert _gains(picam2) == []

    def test_restart_reasserts_the_last_gains(self):
        engine, picam2 = _engine()
        engine.set_grey_world(True)
        _frame(engine, blob(zones(64, GREY / 2, GREY, GREY)))
        engine._apply_controls()
        assert picam2.pushed[-1]["ColourGains"] == _gains(picam2)[0]
        assert picam2.pushed[-1]["AwbEnable"] is False
