# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Sensor mode card, built offscreen without camera or compositor."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6")

from camlab.gui.mode_dialog import ModeCard
from camlab.modes import SensorMode

MODE = SensorMode("SRGGB12_CSI2P", (1920, 1080), 12, 60.0)


def _card(applied: list) -> ModeCard:
    return ModeCard(
        [MODE],
        MODE,
        30.0,
        fps_fixed=True,
        hflip=False,
        on_apply=lambda *sel: applied.append(sel),
        on_cancel=lambda: None,
    )


def test_mirror_toggle_enables_apply(qapp):
    card = _card([])
    assert not card.apply_btn.isEnabled()
    card.mirror_sel.button(True).click()
    assert card.apply_btn.isEnabled()
    card.mirror_sel.button(False).click()
    assert not card.apply_btn.isEnabled()


def test_apply_passes_hflip(qapp):
    applied: list = []
    card = _card(applied)
    card.mirror_sel.button(True).click()
    card.apply_btn.click()
    assert applied == [((1920, 1080), 12, 30.0, True, True)]
