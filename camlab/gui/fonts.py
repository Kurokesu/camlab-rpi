# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Roboto as UI font.

Bundled (assets/Roboto-*.ttf) rather than installed, so every box renders the
same. Medium backs the 500 weight rules in style.py. Missing faces leave Qt on
its own default.
"""

from __future__ import annotations

import logging
import os

from ..qt import QtGui

log = logging.getLogger(__name__)

FAMILY = "Roboto"
_FILES = ("Roboto-Regular.ttf", "Roboto-Medium.ttf")


def load() -> str:
    """Register bundled faces, return the family Qt filed them under."""
    families: list[str] = []
    for name in _FILES:
        path = os.path.join(os.path.dirname(__file__), "..", "assets", name)
        fid = QtGui.QFontDatabase.addApplicationFont(os.path.abspath(path))
        if fid == -1:
            log.warning("could not load %s", name)
            continue
        families += QtGui.QFontDatabase.applicationFontFamilies(fid)
    return FAMILY if FAMILY in families else (families[0] if families else "")


def apply(app) -> None:
    """Set application font. Widgets created later inherit it."""
    family = load()
    if family:
        app.setFont(QtGui.QFont(family))
