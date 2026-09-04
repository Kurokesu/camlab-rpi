# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""CDAF focus sampler: center metric, heat and shared sampling ownership."""

from __future__ import annotations

import numpy as np
import pytest

from camlab.focus_metric import FocusSampler, center_score
from camlab.qt import Qt


class FakeEngine:
    """Engine stand-in handing out one prepared CDAF grid at a time."""

    def __init__(self):
        self.telemetry = type("T", (), {"metadata": {}})()
        self.stats: list[tuple[bool, str]] = []

    def set_stats_output(self, enabled: bool, owner: str = "histogram") -> None:
        self.stats.append((enabled, owner))

    @staticmethod
    def cdaf_focus(metadata: dict):
        return metadata.get("grid")


class TestFocusMetric:
    """A high-pass sum rides scene contrast and exposure, so only ratios read."""

    def _sampler(self):
        engine = FakeEngine()
        sampler = FocusSampler(engine)
        got: list = []
        sampler.sample.connect(got.append, Qt.ConnectionType.DirectConnection)
        return sampler, engine, got

    def _feed(self, sampler, engine, value, grid=None) -> None:
        if grid is None:
            grid = np.full((8, 8), value, dtype=np.uint64)
        engine.telemetry.metadata = {"grid": grid}
        sampler.poll()

    def test_center_score_reads_the_middle_cells(self):
        grid = np.ones((8, 8), dtype=np.uint64)
        grid[3:5, 3:5] = 100
        assert center_score(grid) == pytest.approx(100.0)

    def test_the_metric_is_the_center_of_the_grid(self):
        sampler, engine, got = self._sampler()
        for value in (100, 200, 150):
            self._feed(sampler, engine, value)
        assert [s.raw for s in got] == [100.0, 200.0, 150.0]

    def test_a_missing_blob_holds_the_last_reading(self):
        """libcamera can skip the blob on a frame, and a blink reads as a fault."""
        sampler, engine, got = self._sampler()
        self._feed(sampler, engine, 100)
        engine.telemetry.metadata = {}
        sampler.poll()
        assert got[-1] is got[-2]

    def test_heat_dims_as_sharpness_is_lost(self):
        sampler, engine, got = self._sampler()
        self._feed(sampler, engine, 200)
        self._feed(sampler, engine, 50)
        assert got[0].heat.max() == pytest.approx(1.0)
        assert got[-1].heat.max() == pytest.approx(0.25)

    def test_sampling_lasts_as_long_as_any_readout_shows_it(self):
        """Sheet and map toggle independently, the blob is not free, and it is shared."""
        sampler, engine, _ = self._sampler()
        sampler.set_sampling(True, "map")
        sampler.set_sampling(True, "map")
        sampler.set_sampling(True, "sheet")
        sampler.set_sampling(False, "sheet")
        assert engine.stats == [(True, "focus")]
        assert sampler.sampling
        sampler.set_sampling(False, "map")
        assert engine.stats == [(True, "focus"), (False, "focus")]
        assert not sampler.sampling

    def test_switching_a_readout_on_rewinds_the_peak(self):
        """Or a new reading is judged against a scene its owner never saw."""
        sampler, engine, got = self._sampler()
        sampler.set_sampling(True, "map")
        self._feed(sampler, engine, 200)
        self._feed(sampler, engine, 50)
        assert got[-1].heat.max() == pytest.approx(0.25)
        sampler.set_sampling(True, "sheet")
        self._feed(sampler, engine, 50)
        assert got[-1].heat.max() == pytest.approx(1.0)
