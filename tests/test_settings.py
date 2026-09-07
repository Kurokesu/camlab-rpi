# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Persisted display mode."""

from __future__ import annotations

import json
from pathlib import Path

from camlab.settings import DisplayMode, SettingsStore


def _store(tmp_path: Path) -> SettingsStore:
    return SettingsStore(tmp_path / "state.json")


def test_display_defaults_without_file(tmp_path: Path):
    assert _store(tmp_path).get_display() is DisplayMode.EXTERNAL


def test_display_roundtrip(tmp_path: Path):
    store = _store(tmp_path)
    assert store.set_display(DisplayMode.BOTH) is True
    assert store.get_display() is DisplayMode.BOTH


def test_display_persists_as_plain_string(tmp_path: Path):
    # state.json outlives the code
    store = _store(tmp_path)
    store.set_display(DisplayMode.BOTH)
    assert json.loads(store.path.read_text())["ui"]["display"] == "both"


def test_unknown_display_mode_rejected(tmp_path: Path):
    store = _store(tmp_path)
    store.set_display(DisplayMode.BUILTIN)
    assert store.set_display("sideways") is False
    assert store.get_display() is DisplayMode.BUILTIN


def test_display_survives_other_ui_writes(tmp_path: Path):
    store = _store(tmp_path)
    store.set_display(DisplayMode.BUILTIN)
    store.set_backlight(40)
    assert store.get_display() is DisplayMode.BUILTIN
    assert store.get_backlight() == 40
