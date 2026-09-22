# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""RpiStats against a fake procfs and sysfs: load deltas, RAM and temperatures."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from camlab.stats import RpiStats

GPU_HEADER = "queue     timestamp    jobs   runtime\n"


def gpu_table(rows: dict[str, tuple[int, int]]) -> str:
    """Queue table as v3d prints it, each row keyed by queue to timestamp and runtime."""
    body = "".join(f"{q} {ts} 1 {runtime}\n" for q, (ts, runtime) in rows.items())
    return GPU_HEADER + body


@pytest.fixture
def board(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Fake board sources, so a sampler reads back exactly what a test wrote."""
    v3d = tmp_path / "axi" / "0.v3d"
    v3d.mkdir(parents=True)
    hwmon = tmp_path / "hwmon3"  # index is not boot-stable, discovery goes by name
    hwmon.mkdir()
    (hwmon / "name").write_text("rp1_adc\n")
    monkeypatch.setattr("camlab.stats._PROC_STAT", str(tmp_path / "stat"))
    monkeypatch.setattr("camlab.stats._MEMINFO", str(tmp_path / "meminfo"))
    monkeypatch.setattr("camlab.stats._GPU_STATS_GLOB", str(tmp_path / "axi/*.v3d/gpu_stats"))
    monkeypatch.setattr("camlab.stats._SOC_TEMP", str(tmp_path / "soc_temp"))
    monkeypatch.setattr("camlab.stats._HWMON_GLOB", str(tmp_path / "hwmon*"))
    return SimpleNamespace(
        cpu=lambda jiffies: (tmp_path / "stat").write_text(f"cpu {jiffies}\nintr 0\n"),
        gpu=lambda rows: (v3d / "gpu_stats").write_text(gpu_table(rows)),
        gpu_raw=lambda body: (v3d / "gpu_stats").write_text(body),
        meminfo=lambda body: (tmp_path / "meminfo").write_text(body),
        soc=lambda milli: (tmp_path / "soc_temp").write_text(milli),
        rp1=lambda milli: (hwmon / "temp1_input").write_text(milli),
    )


def primed(board, first: str) -> RpiStats:
    """Sampler that already took one reading, since load needs two to subtract."""
    board.cpu(first)
    rpi = RpiStats()
    rpi.sample()
    return rpi


class TestCpu:
    def test_first_sample_has_no_load_yet(self, board):
        board.cpu("25 0 25 50 0 0 0 0 0 0")
        assert RpiStats().sample().cpu_pct is None

    def test_second_sample_divides_busy_by_total(self, board):
        rpi = primed(board, "0 0 0 0 0 0 0 0 0 0")
        board.cpu("25 0 25 50 0 0 0 0 0 0")
        assert rpi.sample().cpu_pct == pytest.approx(50.0)

    def test_all_idle_reads_as_zero(self, board):
        rpi = primed(board, "0 0 0 0 0 0 0 0 0 0")
        board.cpu("0 0 0 90 10 0 0 0 0 0")
        assert rpi.sample().cpu_pct == pytest.approx(0.0)

    def test_iowait_counts_as_idle(self, board):
        rpi = primed(board, "0 0 0 0 0 0 0 0 0 0")
        board.cpu("50 0 0 0 50 0 0 0 0 0")
        assert rpi.sample().cpu_pct == pytest.approx(50.0)

    def test_stalled_counter_reads_as_unknown(self, board):
        """Equal totals would divide by zero, so no reading beats a wrong one."""
        rpi = primed(board, "25 0 25 50 0 0 0 0 0 0")
        assert rpi.sample().cpu_pct is None

    def test_missing_source_reads_as_unknown(self, board):
        assert RpiStats().sample().cpu_pct is None


class TestGpu:
    def test_first_sample_has_no_load_yet(self, board):
        board.gpu({"bin": (1000, 0)})
        assert RpiStats().sample().gpu_pct is None

    def test_busiest_queue_wins_over_their_sum(self, board):
        """Queues run concurrently, so summing them could read past 100 percent."""
        board.gpu({"bin": (0, 0), "render": (0, 0)})
        rpi = RpiStats()
        rpi.sample()
        board.gpu({"bin": (100, 30), "render": (100, 60)})
        assert rpi.sample().gpu_pct == pytest.approx(60.0)

    def test_runtime_past_the_interval_clamps_to_full(self, board):
        board.gpu({"bin": (0, 0)})
        rpi = RpiStats()
        rpi.sample()
        board.gpu({"bin": (100, 250)})
        assert rpi.sample().gpu_pct == pytest.approx(100.0)

    def test_malformed_row_is_skipped(self, board):
        board.gpu({"bin": (0, 0)})
        rpi = RpiStats()
        rpi.sample()
        board.gpu_raw(GPU_HEADER + "truncated\nbin 100 1 40\n")
        assert rpi.sample().gpu_pct == pytest.approx(40.0)

    def test_missing_node_reads_as_unknown(self, board):
        assert RpiStats().sample().gpu_pct is None


class TestRam:
    def test_used_is_total_less_available(self, board):
        board.meminfo("MemTotal: 1048576 kB\nMemFree: 262144 kB\nMemAvailable: 524288 kB\n")
        sample = RpiStats().sample()
        assert sample.ram_total_mb == pytest.approx(1024.0)
        assert sample.ram_used_mb == pytest.approx(512.0)

    def test_absent_field_drops_both_numbers(self, board):
        board.meminfo("MemTotal: 1048576 kB\n")
        sample = RpiStats().sample()
        assert sample.ram_total_mb is None and sample.ram_used_mb is None

    def test_missing_source_drops_both_numbers(self, board):
        sample = RpiStats().sample()
        assert sample.ram_total_mb is None and sample.ram_used_mb is None


class TestTemperatures:
    def test_millidegrees_read_as_degrees(self, board):
        board.soc("48312\n")
        board.rp1("45000\n")
        sample = RpiStats().sample()
        assert sample.soc_temp_c == pytest.approx(48.312)
        assert sample.rp1_temp_c == pytest.approx(45.0)

    def test_garbage_reads_as_unknown(self, board):
        board.soc("warm\n")
        assert RpiStats().sample().soc_temp_c is None

    def test_missing_source_reads_as_unknown(self, board):
        assert RpiStats().sample().soc_temp_c is None

    def test_hwmon_under_another_name_is_not_read(self, board, tmp_path, monkeypatch):
        other = tmp_path / "other9"
        other.mkdir()
        (other / "name").write_text("cpu_thermal\n")
        (other / "temp1_input").write_text("70000\n")
        monkeypatch.setattr("camlab.stats._HWMON_GLOB", f"{other}*")
        assert RpiStats().sample().rp1_temp_c is None
