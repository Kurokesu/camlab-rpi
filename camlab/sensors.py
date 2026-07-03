"""Sensor registry loader (reads sensors.yaml)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_REGISTRY = Path(__file__).with_name("sensors.yaml")


@dataclass(frozen=True)
class InfoRegister:
    name: str
    addr: int
    len: int = 2


@dataclass(frozen=True)
class Sensor:
    name: str
    overlay: str
    driver_repo: str = ""
    options: tuple[str, ...] = ()
    mono_option: str = ""
    notes: str = ""
    info_registers: tuple[InfoRegister, ...] = field(default_factory=tuple)

    @property
    def has_probe(self) -> bool:
        return bool(self.info_registers)

    @property
    def mono_capable(self) -> bool:
        """True if the sensor has a selectable mono variant (overlay param)."""
        return bool(self.mono_option)


def _coerce_sensor(raw: dict) -> Sensor:
    regs = tuple(
        InfoRegister(name=r["name"], addr=int(r["addr"]), len=int(r.get("len", 2)))
        for r in (raw.get("info_registers") or [])
    )
    return Sensor(
        name=str(raw["name"]),
        overlay=str(raw["overlay"]),
        driver_repo=str(raw.get("driver_repo", "")),
        options=tuple(str(o) for o in (raw.get("options") or [])),
        mono_option=str(raw.get("mono_option", "")),
        notes=str(raw.get("notes", "")),
        info_registers=regs,
    )


class SensorRegistry:
    def __init__(self, sensors: list[Sensor]):
        self._sensors = sensors

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "SensorRegistry":
        path = Path(path) if path else DEFAULT_REGISTRY
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
        sensors = [_coerce_sensor(s) for s in (data.get("sensors") or [])]
        if not sensors:
            raise ValueError(f"no sensors defined in {path}")
        return cls(sensors)

    def __iter__(self):
        return iter(self._sensors)

    def __len__(self):
        return len(self._sensors)

    @property
    def names(self) -> list[str]:
        return [s.name for s in self._sensors]

    def by_name(self, name: str) -> Sensor | None:
        for s in self._sensors:
            if s.name.lower() == name.lower():
                return s
        return None

    def by_overlay(self, overlay: str) -> Sensor | None:
        for s in self._sensors:
            if s.overlay.lower() == overlay.lower():
                return s
        return None
