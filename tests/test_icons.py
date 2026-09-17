# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Bundled subset against names icons.py offers, so a missed rebuild is not silent."""

from __future__ import annotations

import os

import pytest

pytest.importorskip("PyQt6")

from camlab.gui import icons
from camlab.qt import QtWidgets


@pytest.fixture(scope="module")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.mark.parametrize("name", sorted(icons._CODEPOINTS))
def test_every_name_renders_ink(qapp, name):
    image = icons.pixmap(name, 24).toImage()
    inked = any(
        image.pixelColor(x, y).alpha() for y in range(image.height()) for x in range(image.width())
    )
    assert inked, f"{name} is blank, rerun scripts/dev/icon-font.sh"


def test_unlisted_name_renders_blank_rather_than_raising(qapp):
    assert not icons.pixmap("no_such_glyph", 24).isNull()
    assert icons.cached_png("no_such_glyph", 24) is None
