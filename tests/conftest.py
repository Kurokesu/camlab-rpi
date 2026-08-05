# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Shared fixtures: fake DRM sysfs tree under tmp_path."""

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
