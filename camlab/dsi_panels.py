# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""DSI touch panel registry loader (reads data/dsi_panels.yaml)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_REGISTRY = Path(__file__).parent / "data" / "dsi_panels.yaml"


@dataclass(frozen=True)
class Panel:
    name: str
    overlay: str
    notes: str = ""


class PanelRegistry:
    def __init__(self, panels: list[Panel]):
        self._panels = panels

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> PanelRegistry:
        path = Path(path) if path else DEFAULT_REGISTRY
        with open(path, "r") as f:
            data = yaml.safe_load(f) or {}
        panels = [
            Panel(
                name=str(d["name"]),
                overlay=str(d["overlay"]),
                notes=str(d.get("notes", "")),
            )
            for d in (data.get("panels") or [])
        ]
        if not panels:
            raise ValueError(f"no panels defined in {path}")
        return cls(panels)

    @property
    def names(self) -> list[str]:
        return [p.name for p in self._panels]

    def by_name(self, name: str | None) -> Panel | None:
        return next((p for p in self._panels if p.name == name), None)

    def by_overlay(self, token: str | None) -> Panel | None:
        return next((p for p in self._panels if p.overlay == token), None)
