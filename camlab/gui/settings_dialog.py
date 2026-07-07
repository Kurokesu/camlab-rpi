"""Settings card - app-level system options, one row per setting.

Rendered inside a ModalOverlay like the sensor/mode cards. Currently a single
row: the networking toggle. The card reads live state when built. Apply only
acts on rows whose selection changed.
"""

from __future__ import annotations

from collections.abc import Callable

from .. import network
from ..qt import Qt, QtWidgets
from . import icons
from .widgets import SegmentedSelector, hline

_ICON_PX = 20


class SettingsCard(QtWidgets.QFrame):
    def __init__(self, on_apply_network: Callable[[bool], None],
                 on_cancel: Callable[[], None]):
        super().__init__()
        self.setObjectName("modalCard")
        self.setMinimumWidth(420)
        self._on_apply_network = on_apply_network
        self._on_cancel = on_cancel
        self._net_initial = network.is_enabled()

        title = QtWidgets.QLabel("Settings")
        title.setObjectName("modalTitle")

        form = QtWidgets.QFormLayout()
        net_label = QtWidgets.QLabel()
        net_label.setPixmap(icons.pixmap(
            "lan", _ICON_PX, "#98c379" if self._net_initial else "#8a909b"))
        net_row = QtWidgets.QHBoxLayout()
        net_row.setSpacing(8)
        self.net_sel = SegmentedSelector()
        self.net_sel.set_options([("On", True), ("Off", False)],
                                 current=self._net_initial)
        self.net_sel.changed.connect(self._refresh_apply)
        net_row.addWidget(net_label)
        net_row.addWidget(self.net_sel, 1)
        form.addRow("Networking:", net_row)

        note = QtWidgets.QLabel(
            "Off makes the device boot faster. Applies immediately, except "
            "Ethernet stays connected until next boot.")
        note.setObjectName("dialogNote")
        note.setWordWrap(True)
        note.setMaximumWidth(400)

        buttons = QtWidgets.QHBoxLayout()
        cancel_btn = QtWidgets.QPushButton("Cancel")
        cancel_btn.clicked.connect(on_cancel)
        self.apply_btn = QtWidgets.QPushButton("Apply")
        self.apply_btn.clicked.connect(self._apply)
        self.apply_btn.setCursor(Qt.PointingHandCursor)
        # Turning networking off cuts the rig's reachability, so a bare Enter
        # must not trigger it: Cancel is the primary target, same convention
        # as the sensor card.
        self.primary_button = cancel_btn
        buttons.addWidget(cancel_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.apply_btn)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 18)
        lay.setSpacing(14)
        lay.addWidget(title)
        lay.addLayout(form)
        lay.addWidget(note)
        lay.addWidget(hline())
        lay.addLayout(buttons)

        self._refresh_apply()

    def _refresh_apply(self) -> None:
        """Apply is live only when a selection changed."""
        want = bool(self.net_sel.current_value())
        self.apply_btn.setEnabled(want != self._net_initial)

    def _apply(self) -> None:
        want = bool(self.net_sel.current_value())
        if want == self._net_initial:
            self._on_cancel()
            return
        self._on_apply_network(want)
