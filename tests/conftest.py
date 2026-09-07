# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared fixtures: fake DRM and input sysfs trees under tmp_path."""

from __future__ import annotations

from pathlib import Path

import pytest

from camlab import drm


@pytest.fixture
def drm_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point camlab.drm at an empty tree (no connectors)."""
    root = tmp_path / "drm"
    monkeypatch.setattr(drm, "DRM_ROOT", root)
    return root


@pytest.fixture(autouse=True)
def _cold_dsi_display():
    """has_dsi_display caches, so keep the answer from leaking between tests."""
    drm.has_dsi_display.cache_clear()


@pytest.fixture
def fake_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Builder writing {event name: properties bitmask} as input sysfs dirs."""
    root = tmp_path / "input"
    monkeypatch.setattr(drm, "INPUT_ROOT", root)

    def build(devices: dict[str, str]) -> Path:
        for name, props in devices.items():
            d = root / name / "device"
            d.mkdir(parents=True, exist_ok=True)
            (d / "properties").write_text(f"{props}\n")
        return root

    return build


@pytest.fixture
def fake_drm(drm_root: Path):
    """Builder writing {connector name: status} as card1-* sysfs dirs."""

    def build(connectors: dict[str, str]) -> Path:
        for name, status in connectors.items():
            d = drm_root / f"card1-{name}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "status").write_text(f"{status}\n")
        return drm_root

    return build
