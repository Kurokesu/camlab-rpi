# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Material Symbols icon helper.

The bundled font (assets/MaterialSymbolsOutlined.ttf, from Google Fonts) is loaded
once into Qt. Icons are referenced by name and painted from the glyph:

  - pixmap(name, ...) -> a QPixmap, for an inline icon QLabel.
  - icon(name, ...)   -> a QIcon, for QPushButton.setIcon.
  - cached_png(...)   -> a glyph rasterised to a PNG on disk, for stylesheets
    that need an `image: url(...)` (e.g. the checkbox tick).

If the font fails to load (e.g. asset missing), pixmap() returns a transparent
pixmap and icon() an empty QIcon, so the UI degrades to text-only.
"""

from __future__ import annotations

import os
import tempfile

from ..qt import Qt, QtGui

# name -> Material Symbols codepoint (from assets/MaterialSymbolsOutlined.codepoints)
_CODEPOINTS: dict[str, int] = {
    "check": 0xE668,
    "check_circle": 0xF0BE,
    "error": 0xF8B6,
    "warning": 0xF083,
    "power_settings_new": 0xF8C7,
    "photo_camera": 0xE412,
    "terminal": 0xEB8E,
    "tune": 0xE429,
    "close": 0xE5CD,
    "settings": 0xE8B8,
    "lan": 0xEB2F,
    "shutter_speed": 0xE43D,
    "iso": 0xE3F6,
    "wb_sunny": 0xE430,
    "center_focus_weak": 0xE3B5,
    "texture": 0xE421,
    "stroke_partial": 0xF748,
    "bar_chart": 0xE26B,
}

_FAMILY: str | None = None
_LOADED = False


def _ensure_loaded() -> None:
    global _FAMILY, _LOADED
    if _LOADED:
        return
    _LOADED = True
    path = os.path.join(os.path.dirname(__file__), "..", "assets",
                        "MaterialSymbolsOutlined.ttf")
    fid = QtGui.QFontDatabase.addApplicationFont(os.path.abspath(path))
    if fid != -1:
        families = QtGui.QFontDatabase.applicationFontFamilies(fid)
        if families:
            _FAMILY = families[0]


def _glyph(name: str) -> str:
    _ensure_loaded()
    cp = _CODEPOINTS.get(name)
    if _FAMILY is None or cp is None:
        return ""
    return chr(cp)


def pixmap(name: str, size_px: int, color: str = "#d7dae0") -> QtGui.QPixmap:
    glyph = _glyph(name)
    pm = QtGui.QPixmap(size_px, size_px)
    pm.fill(Qt.GlobalColor.transparent)
    if not glyph:
        return pm
    painter = QtGui.QPainter(pm)
    font = QtGui.QFont(_FAMILY)
    font.setPixelSize(size_px)
    painter.setFont(font)
    painter.setPen(QtGui.QColor(color))
    painter.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, glyph)
    painter.end()
    return pm


def icon(name: str, size_px: int = 22, color: str = "#d7dae0") -> QtGui.QIcon:
    return QtGui.QIcon(pixmap(name, size_px, color))


_PNG_CACHE: dict[tuple, str] = {}


def cached_png(name: str, size_px: int, color: str = "#d7dae0") -> str | None:
    """Render a glyph to a PNG on disk and return its path.

    For Qt stylesheets that need an `image: url(...)`, e.g. the checkbox tick.
    Returns None if the icon font is unavailable.
    """
    if not _glyph(name):
        return None
    key = (name, size_px, color)
    cached = _PNG_CACHE.get(key)
    if cached and os.path.exists(cached):
        return cached
    out_dir = os.path.join(tempfile.gettempdir(), "camlab-icons")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{name}-{size_px}-{color.lstrip('#')}.png")
    pixmap(name, size_px, color).save(path, "PNG")
    _PNG_CACHE[key] = path
    return path
