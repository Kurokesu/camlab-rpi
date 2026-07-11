# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Application stylesheet, set once on MainWindow.

Everything is styled from this one QSS blob plus two generated pieces: the
modal-card glass (derived from the sheet glass constants so sheets and modals
stay in lockstep) and the checkbox tick (a glyph rasterised to a PNG, since
QSS sub-controls need an image url).
"""

from __future__ import annotations

from ..qt import QtGui
from . import icons
from .control_sheet import GLASS_BG, GLASS_BORDER

_STYLE = """
QWidget { background: #1b1d22; color: #d7dae0; font-size: 13px; }
QFrame#statusStrip { background: #23262d; border-bottom: 1px solid #2f333c; }
QFrame#controls { background: #1b1d22; border-top: 1px solid #2f333c; }
QFrame#vsep, QFrame#hsep { background: #3a3f4b; }
QFrame#statusStrip QWidget { background: transparent; }
QFrame#statusStrip QFrame#vsep { background: #3a3f4b; }
QLabel#telemetry { color: #c4c9d2; }
QLabel#bootInfo { color: #8a909b; }
QLabel#version { color: #8a909b; }
QLabel#rpiStats { color: #8a909b; }
QLabel#errCount[sev="ok"], QLabel#warnCount[sev="ok"] { color: #98c379; }
QLabel#errCount[sev="alert"]  { color: #e06c75; font-weight: 600; }
QLabel#warnCount[sev="alert"] { color: #e5c07b; font-weight: 600; }
QPushButton { background: #2c303a; border: 1px solid #3a3f4b; border-radius: 5px;
              padding: 6px 12px; }
QPushButton:hover { background: #353b47; }
QPushButton:disabled { background: #23262d; border-color: #2f333c; color: #5c6370; }
QPushButton:checked { background: #3d4858; border-color: #7f8aa0; color: #ffffff; }
QPushButton:focus { border-color: #7aa2f7; background: #353b47; outline: none; }
QPushButton#danger { border-color: #803126; }
QPushButton#danger:disabled { border-color: #4a2620; }
QPushButton#danger:hover { background: #50211a; }
QPushButton#danger:focus { border-color: #e06c75; background: #50211a; outline: none; }
QPushButton#segment { background: #262a33; border: 1px solid #3a3f4b; border-radius: 0;
                      padding: 6px 14px; color: #c4c9d2; }
QPushButton#segment[pos="mid"], QPushButton#segment[pos="last"] { margin-left: -1px; }
QPushButton#segment[pos="first"] { border-top-left-radius: 6px; border-bottom-left-radius: 6px; }
QPushButton#segment[pos="last"] { border-top-right-radius: 6px; border-bottom-right-radius: 6px; }
QPushButton#segment[pos="only"] { border-radius: 6px; }
QPushButton#segment:hover { background: #2f3540; }
QPushButton#segment:checked { background: #3d4858; border-color: #7f8aa0; color: #ffffff; }
QPushButton#segment:checked:disabled { background: #2f3540; border-color: #4a505c; color: #aeb4bf; }
QPushButton#segment:focus { border-color: #7aa2f7; background: #2f3949; outline: none; }
QPushButton#segment:checked:focus { border-color: #9db8ff; background: #45526a; color: #ffffff; }
QPushButton#chip { text-align: left; }
QPushButton[manual="true"] { border-color: #7f6a3d; color: #e5c07b; }
QPushButton[manual="true"]:checked { background: #4a4231; border-color: #b08d3f; color: #f0d493; }
QFrame#controlSheet { background: transparent; }
QFrame#controlSheet QWidget { background: transparent; }
QLabel#sheetTitle { color: #aeb4bf; font-weight: 600; }
QLabel#sheetCaption { color: #8a909b; }
QLabel#sheetValue { color: #e8eaed; font-size: 14px; }
QLabel#sheetCaption[dim="true"] { color: #565c66; }
QLabel#sheetValue[dim="true"] { color: #6a707a; }
QFrame#controlSheet QPushButton#segment { background: #262a33; }
QFrame#controlSheet QPushButton#segment:checked { background: #3d4858; }
QFrame#controlSheet QPushButton#segment:focus { background: #2f3949; }
QFrame#controlSheet QPushButton#segment:checked:focus { background: #45526a; }
QSlider::groove:horizontal { height: 6px; background: #2c303a;
                             border: 1px solid #3a3f4b; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #56617a; border: 1px solid #3a3f4b;
                               border-radius: 3px; }
QSlider::handle:horizontal { width: 18px; margin: -7px 0; border-radius: 10px;
                             background: #c4c9d2; border: 1px solid #7f8aa0; }
QSlider::handle:horizontal:hover { background: #e8eaed; }
QSlider[auto="true"]::handle:horizontal { background: #5c6370; border-color: #4a505c; }
QSlider[auto="true"]::sub-page:horizontal { background: #353b47; }
QSlider:focus { outline: none; }
QSlider:focus::handle:horizontal { border-color: #7aa2f7; }
QCheckBox { color: #aeb4bf; spacing: 6px; }
QCheckBox::indicator { width: 20px; height: 20px; border: 1px solid #4a505c;
                       border-radius: 4px; background: #2c303a; }
QCheckBox::indicator:hover { border-color: #6a7180; }
QCheckBox::indicator:checked { border-color: #6a7180; }
QCheckBox::indicator:checked:hover { border-color: #808998; }
QPlainTextEdit#logView { background: #15171b; border: none; color: #c4c9d2; }
QLabel#logTitle { color: #8a909b; font-weight: 600; }
QLabel#dialogNote { color: #8a909b; }
QFrame#modalCard QWidget { background: transparent; }
QFrame#modalCard QFrame#hsep { background: #3a3f4b; }
QFrame#modalCard QPushButton { background: #2c303a; }
QFrame#modalCard QPushButton:hover { background: #353b47; }
QFrame#modalCard QPushButton:focus { background: #353b47; }
QFrame#modalCard QPushButton:disabled { background: #23262d; }
QFrame#modalCard QPushButton#danger:hover { background: #50211a; }
QFrame#modalCard QPushButton#danger:focus { background: #50211a; }
QFrame#modalCard QPushButton#segment { background: #262a33; }
QFrame#modalCard QPushButton#segment:hover { background: #2f3540; }
QFrame#modalCard QPushButton#segment:checked { background: #3d4858; }
QFrame#modalCard QPushButton#segment:checked:disabled { background: #2f3540; }
QFrame#modalCard QPushButton#segment:focus { background: #2f3949; }
QFrame#modalCard QPushButton#segment:checked:focus { background: #45526a; }
QLabel#modalTitle { font-size: 16px; font-weight: 600; color: #e8eaed; }
QLabel#modalText { color: #aeb4bf; }
QLabel#modalHint { color: #9aa1ac; font-size: 12px; }
"""


def _rgba(c: QtGui.QColor) -> str:
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {c.alpha()})"


def build_stylesheet() -> str:
    # Modal cards wear the same glass as the sheets. Sheets paint it directly
    # in paintEvent, cards get it via QSS.
    glass = (f"QFrame#modalCard {{ background: {_rgba(GLASS_BG)};"
             f" border-radius: 10px;"
             f" border: 1px solid {_rgba(GLASS_BORDER)}; }}\n")
    tick = icons.cached_png("check", 17, "#cdd3dd")
    tick_rule = f"QCheckBox::indicator:checked {{ image: url({tick}); }}" if tick else ""
    return _STYLE + glass + tick_rule
