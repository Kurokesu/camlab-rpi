# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Power card: Reboot and Shutdown as glyph tiles, Cancel plain beneath them.

Rendered inside ModalOverlay. Cancel takes Enter, so neither action is one
keypress away. Amber marks the recoverable choice, red the terminal one.
"""

from __future__ import annotations

from collections.abc import Callable

from ..qt import Qt, QtGui, QtWidgets
from . import icons
from .style import SEV_COLOR, SHUTDOWN_TINT

_GLYPH_PX = 36
_TILE_W = 170
_TILE_H = 104
_CANCEL_H = 48
_REBOOT_TINT = SEV_COLOR["warning"]


class _Tile(QtWidgets.QPushButton):
    """Glyph over label. Frame and colour come from shared button QSS."""

    def __init__(self, label: str, glyph: QtGui.QPixmap):
        super().__init__()
        # Button carries no text of its own, so its name lives here
        self.setAccessibleName(label)
        self.setMinimumSize(_TILE_W, _TILE_H)
        glyph_lbl = QtWidgets.QLabel()
        glyph_lbl.setPixmap(glyph)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addStretch(1)
        for child in (glyph_lbl, QtWidgets.QLabel(label)):
            # Press must reach button, not stop at child under it
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            lay.addWidget(child, 0, Qt.AlignmentFlag.AlignHCenter)
        lay.addStretch(1)


class PowerCard(QtWidgets.QFrame):
    def __init__(
        self,
        on_reboot: Callable[[], None],
        on_shutdown: Callable[[], None],
        on_cancel: Callable[[], None],
    ):
        super().__init__()
        self.setObjectName("modalCard")

        title = QtWidgets.QLabel("Shutdown options")
        title.setObjectName("modalTitle")

        self.reboot_btn = _Tile("Reboot", icons.pixmap("restart_alt", _GLYPH_PX, _REBOOT_TINT))
        self.reboot_btn.setProperty("sev", "warning")
        self.reboot_btn.clicked.connect(on_reboot)
        self.shutdown_btn = _Tile(
            "Shutdown", icons.pixmap("power_settings_new", _GLYPH_PX, SHUTDOWN_TINT)
        )
        self.shutdown_btn.setObjectName("danger")
        self.shutdown_btn.clicked.connect(on_shutdown)
        tiles = QtWidgets.QHBoxLayout()
        tiles.setSpacing(12)
        tiles.addWidget(self.reboot_btn, 1)
        tiles.addWidget(self.shutdown_btn, 1)

        # Drawn no differently: nothing on this card is a recommendation
        cancel = QtWidgets.QPushButton("Cancel")
        cancel.setMinimumHeight(_CANCEL_H)
        cancel.clicked.connect(on_cancel)
        self.primary_button = cancel

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.setSpacing(14)
        lay.addWidget(title)
        lay.addLayout(tiles)
        lay.addWidget(cancel)
