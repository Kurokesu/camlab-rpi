# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Boot lores sizing follows the display setting."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from camlab.qt import QtCore
from camlab.settings import DisplayMode

app_module = pytest.importorskip("camlab.app")

PANEL_RECT = QtCore.QRect(0, 0, 800, 480)
MONITOR_RECT = QtCore.QRect(800, 0, 1920, 1080)
UNION_RECT = QtCore.QRect(0, 0, 2720, 1080)


def _screen(name: str, geometry: QtCore.QRect, virtual: QtCore.QRect) -> SimpleNamespace:
    return SimpleNamespace(
        name=lambda: name, geometry=lambda: geometry, virtualGeometry=lambda: virtual
    )


def _app(*screens: SimpleNamespace) -> SimpleNamespace:
    """First screen is primary, Cage puts the panel at the layout origin."""
    primary = screens[0] if screens else None
    return SimpleNamespace(screens=lambda: list(screens), primaryScreen=lambda: primary)


@pytest.fixture(autouse=True)
def _no_forced_screen(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("CAMLAB_SCREEN", raising=False)


PANEL = _screen("DSI-2", PANEL_RECT, UNION_RECT)
MONITOR = _screen("HDMI-A-1", MONITOR_RECT, UNION_RECT)


def test_both_sizes_to_monitor_pane():
    assert app_module._avail_size(_app(PANEL, MONITOR), DisplayMode.BOTH) == (
        1920,
        1080 - app_module._CHROME_PX,
    )


def test_both_without_monitor_falls_back_to_primary():
    assert app_module._avail_size(_app(PANEL), DisplayMode.BOTH) == (
        800,
        480 - app_module._CHROME_COMPACT_PX,
    )


@pytest.mark.parametrize("mode", [DisplayMode.EXTERNAL, DisplayMode.BUILTIN])
def test_other_modes_use_primary_screen(mode: DisplayMode):
    assert app_module._avail_size(_app(PANEL, MONITOR), mode) == (
        800,
        480 - app_module._CHROME_COMPACT_PX,
    )


def test_no_screens_uses_default():
    assert app_module._avail_size(_app(), DisplayMode.BOTH) == (1280, 720)
