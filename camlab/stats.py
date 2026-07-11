# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""RpiStats - board health facts (CPU, RAM, GPU, SoC temperature).

Everything comes from procfs/sysfs, no subprocess calls. CPU and GPU loads
are derived from busy-time deltas between samples, so the first sample()
reports them as None. Any source that is missing on a given kernel simply
yields None and the GUI drops that field.
"""

from __future__ import annotations

import glob
from dataclasses import dataclass

# v3d exposes per-queue cumulative busy ns. Bin and render carry the real
# rasterisation work (tfu/csd are transfer/compute, rarely the bottleneck).
_GPU_STATS_GLOB = "/sys/devices/platform/axi/*.v3d/gpu_stats"
_SOC_TEMP = "/sys/class/thermal/thermal_zone0/temp"


@dataclass
class RpiStatsSample:
    cpu_pct: float | None = None
    gpu_pct: float | None = None
    ram_used_mb: float | None = None
    ram_total_mb: float | None = None
    soc_temp_c: float | None = None


class RpiStats:
    def __init__(self) -> None:
        self._cpu_prev: tuple[int, int] | None = None   # (busy, total) jiffies
        self._gpu_prev: dict[str, tuple[int, int]] = {}  # queue -> (ts, runtime)

    def sample(self) -> RpiStatsSample:
        return RpiStatsSample(
            cpu_pct=self._cpu(), gpu_pct=self._gpu(),
            **self._ram(), soc_temp_c=self._temp())

    def _cpu(self) -> float | None:
        try:
            with open("/proc/stat") as f:
                fields = [int(v) for v in f.readline().split()[1:]]
        except (OSError, ValueError):
            return None
        idle = fields[3] + fields[4]  # idle + iowait
        total = sum(fields)
        prev, self._cpu_prev = self._cpu_prev, (total - idle, total)
        if prev is None or total == prev[1]:
            return None
        return 100.0 * (total - idle - prev[0]) / (total - prev[1])

    def _gpu(self) -> float | None:
        paths = glob.glob(_GPU_STATS_GLOB)
        if not paths:
            return None
        busiest = None
        try:
            with open(paths[0]) as f:
                lines = f.readlines()[1:]  # skip header
        except OSError:
            return None
        for line in lines:
            parts = line.split()
            if len(parts) != 4:
                continue
            queue, ts, _jobs, runtime = parts[0], int(parts[1]), parts[2], int(parts[3])
            prev = self._gpu_prev.get(queue)
            self._gpu_prev[queue] = (ts, runtime)
            if prev is None or ts == prev[0]:
                continue
            load = 100.0 * (runtime - prev[1]) / (ts - prev[0])
            # Queues run concurrently, so overall business is the busiest one,
            # not the sum (which could read past 100%).
            if busiest is None or load > busiest:
                busiest = load
        return None if busiest is None else min(max(busiest, 0.0), 100.0)

    @staticmethod
    def _ram() -> dict:
        try:
            fields = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    key, _, rest = line.partition(":")
                    fields[key] = int(rest.split()[0])  # kB
            total = fields["MemTotal"]
            avail = fields["MemAvailable"]
        except (OSError, KeyError, ValueError):
            return {"ram_used_mb": None, "ram_total_mb": None}
        return {"ram_used_mb": (total - avail) / 1024.0,
                "ram_total_mb": total / 1024.0}

    @staticmethod
    def _temp() -> float | None:
        try:
            with open(_SOC_TEMP) as f:
                return int(f.read().strip()) / 1000.0
        except (OSError, ValueError):
            return None
