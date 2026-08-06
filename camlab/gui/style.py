# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Application stylesheet and layout profiles.

One QSS blob plus profile-sized rules, modal glass and checkbox tick PNG.
REGULAR for HDMI monitors, COMPACT for small touch panels (800x480 class).
Profile picked from active screen, re-applied on display switch.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..qt import QtGui
from . import icons

# Glass background/border shared by sheets (painted) and modal cards (QSS).
# Alpha tuned so live picture reads through while labels keep contrast.
GLASS_BG = QtGui.QColor(24, 26, 32, 175)
GLASS_BORDER = QtGui.QColor(70, 76, 90, 200)

# Severity accents, mirroring the [sev=...] rules in the stylesheet below.
SEV_COLOR = {"error": "#e06c75", "warning": "#e5c07b"}


@dataclass(frozen=True)
class UiProfile:
    """Pixel sizes that differ between the monitor and touch-panel layouts."""

    compact: bool
    font_px: int
    sheet_value_font_px: int
    modal_title_font_px: int
    modal_hint_font_px: int
    icon_px: int
    slider_dial_px: int
    slider_groove_px: int
    button_pad: tuple[int, int]  # (vertical, horizontal)
    segment_pad: tuple[int, int]
    checkbox_px: int
    row_margin: int  # controls row content margins
    row_spacing: int
    sheet_title_w: int  # 0 = hug contents
    sheet_value_w: int
    zebra_slider_w: int


REGULAR = UiProfile(
    compact=False,
    font_px=13,
    sheet_value_font_px=14,
    modal_title_font_px=16,
    modal_hint_font_px=12,
    icon_px=21,
    slider_dial_px=18,
    slider_groove_px=6,
    button_pad=(6, 12),
    segment_pad=(6, 14),
    checkbox_px=20,
    row_margin=10,
    row_spacing=8,
    sheet_title_w=110,
    sheet_value_w=80,
    zebra_slider_w=260,
)

# 13 px is ~1.5 mm on 800x480 4.3" panel: unreadable. 30 px touch targets unhittable.
# Larger type, thicker sliders, taller buttons.
COMPACT = UiProfile(
    compact=True,
    font_px=16,
    sheet_value_font_px=16,
    modal_title_font_px=18,
    modal_hint_font_px=13,
    icon_px=22,
    slider_dial_px=28,
    slider_groove_px=8,
    button_pad=(10, 5),
    segment_pad=(10, 14),
    checkbox_px=24,
    row_margin=6,
    row_spacing=4,
    sheet_title_w=0,
    sheet_value_w=96,
    zebra_slider_w=170,
)

# Anything at or below this height is a small touch panel, not a monitor.
_COMPACT_MAX_HEIGHT = 600


def profile_for_screen(screen) -> UiProfile:
    if screen is None:
        return REGULAR
    return COMPACT if screen.geometry().height() <= _COMPACT_MAX_HEIGHT else REGULAR


_STYLE = """
QWidget { background: #1b1d22; color: #d7dae0; }
QFrame#statusStrip { background: #23262d; border-bottom: 1px solid #2f333c; }
QFrame#controls { background: #1b1d22; border-top: 1px solid #2f333c; }
QFrame#vsep, QFrame#hsep { background: #3a3f4b; }
QFrame#statusStrip QWidget { background: transparent; }
QFrame#statusStrip QFrame#vsep { background: #3a3f4b; }
QLabel#telemetry { color: #c4c9d2; }
QLabel#bootInfo { color: #8a909b; }
QLabel#version { color: #8a909b; }
QLabel#rpiStats { color: #8a909b; }
QLabel#statsCard { background: transparent; color: #c4c9d2; }
QPushButton { background: #2c303a; border: 1px solid #3a3f4b; border-radius: 5px; }
QPushButton:disabled { background: #23262d; border-color: #2f333c; color: #5c6370; }
QPushButton:checked { background: #3d4858; border-color: #7f8aa0; color: #ffffff; }
QPushButton:focus { border-color: #7aa2f7; background: #353b47; outline: none; }
QPushButton#danger { border-color: #803126; }
QPushButton#danger:disabled { border-color: #4a2620; }
QPushButton#danger:focus { border-color: #e06c75; background: #50211a; outline: none; }
QPushButton#segment { background: #262a33; border: 1px solid #3a3f4b; border-radius: 0;
                      color: #c4c9d2; }
QPushButton#segment[pos="mid"], QPushButton#segment[pos="last"] { margin-left: -1px; }
QPushButton#segment[pos="first"] { border-top-left-radius: 6px; border-bottom-left-radius: 6px; }
QPushButton#segment[pos="last"] { border-top-right-radius: 6px; border-bottom-right-radius: 6px; }
QPushButton#segment[pos="only"] { border-radius: 6px; }
QPushButton#segment:checked { background: #3d4858; border-color: #7f8aa0; color: #ffffff; }
QPushButton#segment:checked:disabled { background: #2f3540; border-color: #4a505c; color: #aeb4bf; }
QPushButton#segment:focus { border-color: #7aa2f7; background: #2f3949; outline: none; }
QPushButton#segment:checked:focus { border-color: #9db8ff; background: #45526a; color: #ffffff; }
QPushButton#segment[sev="warning"], QPushButton#segment[sev="warning"]:checked { color: #e5c07b; }
QPushButton#segment[sev="error"], QPushButton#segment[sev="error"]:checked { color: #e06c75; }
QPushButton#chip { text-align: left; }
QPushButton[manual="true"] { border-color: #7f6a3d; color: #e5c07b; }
QPushButton[manual="true"]:checked { background: #4a4231; border-color: #b08d3f; color: #f0d493; }
QPushButton[sev="warning"] { border-color: #7f6a3d; }
QPushButton[sev="error"] { border-color: #803126; }
QFrame#controlSheet { background: transparent; }
QFrame#controlSheet QWidget { background: transparent; }
QLabel#sheetTitle { color: #aeb4bf; font-weight: 600; }
QLabel#sheetCaption { color: #8a909b; }
QLabel#sheetValue { color: #e8eaed; }
QLabel#sheetCaption[dim="true"] { color: #565c66; }
QLabel#sheetValue[dim="true"] { color: #6a707a; }
QFrame#controlSheet QPushButton#segment { background: #262a33; }
QFrame#controlSheet QPushButton#segment:checked { background: #3d4858; }
QFrame#controlSheet QPushButton#segment:focus { background: #2f3949; }
QFrame#controlSheet QPushButton#segment:checked:focus { background: #45526a; }
QCheckBox { color: #aeb4bf; spacing: 6px; }
QCheckBox::indicator { border: 1px solid #4a505c; border-radius: 4px; background: #2c303a; }
QCheckBox::indicator:checked { border-color: #6a7180; }
QTextEdit#logView { background: #15171b; border: none; color: #c4c9d2; }
QTextEdit#logView QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QTextEdit#logView QScrollBar::handle:vertical { background: #3a3f4b; border-radius: 5px;
                                               min-height: 28px; }
QTextEdit#logView QScrollBar::add-line:vertical,
QTextEdit#logView QScrollBar::sub-line:vertical { height: 0; }
QTextEdit#logView QScrollBar::add-page:vertical,
QTextEdit#logView QScrollBar::sub-page:vertical { background: transparent; }
QLabel#logTitle { color: #8a909b; font-weight: 600; }
QLabel#dialogNote { color: #8a909b; }
QFrame#modalCard QWidget { background: transparent; }
QFrame#modalCard QFrame#hsep { background: #3a3f4b; }
QFrame#modalCard QPushButton { background: #2c303a; }
QFrame#modalCard QPushButton:focus { background: #353b47; }
QFrame#modalCard QPushButton:disabled { background: #23262d; }
QFrame#modalCard QPushButton#danger:focus { background: #50211a; }
QFrame#modalCard QPushButton#segment { background: #262a33; }
QFrame#modalCard QPushButton#segment:checked { background: #3d4858; }
QFrame#modalCard QPushButton#segment:checked:disabled { background: #2f3540; }
QFrame#modalCard QPushButton#segment:focus { background: #2f3949; }
QFrame#modalCard QPushButton#segment:checked:focus { background: #45526a; }
QLabel#modalTitle { font-weight: 600; color: #e8eaed; }
QLabel#modalText { color: #aeb4bf; }
QLabel#modalHint { color: #9aa1ac; }
"""

# Hover is mouse-only feedback: tap parks synthesized mouse on widget, pinning :hover until next tap.
# Compact skips these rules. Prepended so checked/focus rules win equal-specificity ties.
_HOVER = """
QPushButton:hover { background: #353b47; }
QPushButton#danger:hover { background: #50211a; }
QPushButton#segment:hover { background: #2f3540; }
QCheckBox::indicator:hover { border-color: #6a7180; }
QCheckBox::indicator:checked:hover { border-color: #808998; }
QTextEdit#logView QScrollBar::handle:vertical:hover { background: #4a505c; }
QFrame#modalCard QPushButton:hover { background: #353b47; }
QFrame#modalCard QPushButton#danger:hover { background: #50211a; }
QFrame#modalCard QPushButton#segment:hover { background: #2f3540; }
QSlider::handle:horizontal:hover { background: #e8eaed; }
"""


def _rgba(c: QtGui.QColor) -> str:
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha()})"


def _slider_rules(p: UiProfile) -> str:
    # Hit box follows dial. Groove remains independent.
    margin = (p.slider_dial_px - p.slider_groove_px) // 2
    radius = (p.slider_dial_px + 1) // 2
    groove_radius = p.slider_groove_px // 2
    return f"""
QSlider {{ min-height: {p.slider_dial_px}px; }}
QSlider::groove:horizontal {{ height: {p.slider_groove_px}px; background: #2c303a;
                             border: 1px solid #3a3f4b;
                             border-radius: {groove_radius}px; }}
QSlider::sub-page:horizontal {{ background: #56617a; border: 1px solid #3a3f4b;
                               border-radius: {groove_radius}px; }}
QSlider::handle:horizontal {{ width: {p.slider_dial_px}px;
                             margin: -{margin}px 0;
                             border-radius: {radius}px;
                             background: #c4c9d2; border: 1px solid #7f8aa0; }}
QSlider[auto="true"]::handle:horizontal {{ background: #5c6370;
                                          border-color: #4a505c; }}
QSlider[auto="true"]::sub-page:horizontal {{ background: #353b47; }}
QSlider:focus {{ outline: none; }}
QSlider:focus::handle:horizontal {{ border-color: #7aa2f7; }}
"""


def _profile_rules(p: UiProfile) -> str:
    """Sizes that scale with the profile (fonts, paddings, indicators)."""
    return f"""
QWidget {{ font-size: {p.font_px}px; }}
QPushButton {{ padding: {p.button_pad[0]}px {p.button_pad[1]}px; }}
QPushButton#segment {{ padding: {p.segment_pad[0]}px {p.segment_pad[1]}px; }}
QCheckBox::indicator {{ width: {p.checkbox_px}px; height: {p.checkbox_px}px; }}
QLabel#sheetValue {{ font-size: {p.sheet_value_font_px}px; }}
QLabel#modalTitle {{ font-size: {p.modal_title_font_px}px; }}
QLabel#modalHint {{ font-size: {p.modal_hint_font_px}px; }}
"""


def build_stylesheet(profile: UiProfile = REGULAR) -> str:
    # Modal cards wear same glass as sheets. Sheets paint in paintEvent, cards via QSS.
    glass = (
        f"QFrame#modalCard {{ background: {_rgba(GLASS_BG)};"
        f" border-radius: 10px;"
        f" border: 1px solid {_rgba(GLASS_BORDER)}; }}\n"
    )
    tick = icons.cached_png("check", profile.checkbox_px - 3, "#cdd3dd")
    tick_rule = f"QCheckBox::indicator:checked {{ image: url({tick}); }}" if tick else ""
    hover = "" if profile.compact else _HOVER
    return hover + _STYLE + _profile_rules(profile) + _slider_rules(profile) + glass + tick_rule
