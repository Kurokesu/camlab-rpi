# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Kernel driver lines, boot backlog and Log button tint.

MainWindow is too heavy to build here, so each method runs unbound against a stub.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("picamera2")
pytest.importorskip("PyQt6")

from conftest import PROBE_FAILURE

from camlab import dmesg
from camlab.gui.main_window import MainWindow
from camlab.integrity import APP_CATEGORY, IntegrityStats, LineSource


def stub(overlay: str, model: str = "") -> SimpleNamespace:
    shown: list[str] = []
    fed: list[str] = []
    return SimpleNamespace(
        config=SimpleNamespace(get_current=lambda: {"overlay": overlay}),
        engine=SimpleNamespace(info={"Model": model} if model else {}),
        log_panel=SimpleNamespace(append_line=shown.append),
        monitor=SimpleNamespace(feed=fed.append),
        shown=shown,
        fed=fed,
    )


def wire_stub(capture: LineSource) -> SimpleNamespace:
    """What _wire reaches for, capture real and every other signal inert."""
    inert = SimpleNamespace(connect=lambda *_: None)
    shown: list[str] = []
    fed: list[str] = []
    return SimpleNamespace(
        capture=capture,
        log_panel=SimpleNamespace(append_line=shown.append, update_integrity=None, cleared=inert),
        monitor=SimpleNamespace(feed=fed.append, stats_changed=inert, reset=None),
        status=SimpleNamespace(stats_tapped=inert),
        viewfinder_area=SimpleNamespace(tapped=inert, toggle_stats_overlay=None),
        engine=SimpleNamespace(on_first_frame=lambda _cb: None),
        _on_integrity=None,
        _on_viewfinder_tapped=None,
        _on_first_frame=None,
        shown=shown,
        fed=fed,
    )


def tint_stub() -> SimpleNamespace:
    """What _on_integrity reaches for, the button restyle inert."""
    return SimpleNamespace(
        log_btn=SimpleNamespace(isChecked=lambda: False),
        _sync_log_button=lambda _checked: None,
    )


@pytest.mark.parametrize(
    ("stats", "sev"),
    [
        (IntegrityStats({"error": {APP_CATEGORY: 1}}), "error"),
        (IntegrityStats({"warning": {APP_CATEGORY: 8}}), ""),
        (IntegrityStats({"warning": {"stack_pairing": 1}}), "warning"),
        (IntegrityStats({"warning": {APP_CATEGORY: 8, "stack_pairing": 1}}), "warning"),
    ],
)
def test_log_button_tint_skips_app_warnings(stats, sev):
    """App errors tint, app warnings do not, and volume does not override category."""
    win = tint_stub()
    MainWindow._on_integrity(win, stats)
    assert win._sev == sev


def test_early_records_replay_once_panel_exists():
    """Camera open runs before the window, so its error reaches panel and tally on replay."""
    capture = LineSource()
    line = "12:50:03 ERROR camlab: camera open failed: no camera enumerated by libcamera"
    capture._deliver(line)
    win = wire_stub(capture)
    MainWindow._wire(win)
    assert win.shown == [line]
    assert win.fed == [line]


def test_missing_camera_pushes_driver_lines(monkeypatch):
    monkeypatch.setattr(dmesg, "read", lambda module: PROBE_FAILURE)
    win = stub("ar0822")
    MainWindow._report_driver_errors(win)
    assert win.shown == PROBE_FAILURE
    assert win.fed == PROBE_FAILURE


def test_scrape_asks_for_selected_overlay(monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr(dmesg, "read", lambda module: asked.append(module) or [])
    MainWindow._report_driver_errors(stub("imx585"))
    assert asked == ["imx585"]


@pytest.mark.parametrize(
    ("overlay", "model"),
    [("ar0822", "ar0822"), ("", "")],
)
def test_scrape_stays_off_without_no_camera_path(monkeypatch, overlay, model):
    monkeypatch.setattr(dmesg, "read", lambda module: pytest.fail("scraped the ring buffer"))
    win = stub(overlay, model)
    MainWindow._report_driver_errors(win)
    assert win.shown == []
