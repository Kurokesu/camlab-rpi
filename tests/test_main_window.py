# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""No-camera path: kernel driver lines join the log stream.

MainWindow is too heavy to build here, so the method runs unbound against a stub.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("picamera2")
pytest.importorskip("PyQt6")

from conftest import PROBE_FAILURE

from camlab import dmesg
from camlab.gui.main_window import MainWindow


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
def test_scrape_stays_off_without_the_no_camera_path(monkeypatch, overlay, model):
    monkeypatch.setattr(dmesg, "read", lambda module: pytest.fail("scraped the ring buffer"))
    win = stub(overlay, model)
    MainWindow._report_driver_errors(win)
    assert win.shown == []
