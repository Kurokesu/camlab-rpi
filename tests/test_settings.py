# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Persisted display mode."""

from __future__ import annotations

from pathlib import Path

from camlab.settings import DISPLAY_DEFAULT, SettingsStore


def _store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "state.json")


def test_display_defaults_without_file(tmp_path: Path):
    assert _store(tmp_path).get_display() == DISPLAY_DEFAULT


def test_display_roundtrip(tmp_path: Path):
    store = _store(tmp_path)
    assert store.set_display("both") is True
    assert store.get_display() == "both"


def test_unknown_display_mode_rejected(tmp_path: Path):
    store = _store(tmp_path)
    store.set_display("builtin")
    assert store.set_display("sideways") is False
    assert store.get_display() == "builtin"


def test_display_survives_other_ui_writes(tmp_path: Path):
    store = _store(tmp_path)
    store.set_display("builtin")
    store.set_backlight(40)
    assert store.get_display() == "builtin"
    assert store.get_backlight() == 40
