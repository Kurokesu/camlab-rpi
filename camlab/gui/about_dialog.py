# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""About card: versions this box runs, and the updates for them.

Rows and blocked reason come from dpkg at open time, so the card works with
networking off and never repeats what an older check recorded. Checking is a
manual act and never turns networking on. It runs the privileged shim through
QProcess, an apt refresh over a slow link would freeze the kiosk for a minute.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from .. import network, updater
from ..qt import Qt, QtCore, QtWidgets
from .widgets import hline, kinetic_scroll

log = logging.getLogger(__name__)

_CHECK_TIMEOUT_MS = 120_000
# Version column: one Kurokesu version fits a line, capped so a pair of them
# wraps instead of widening the card past the panel.
_VERSION_MIN = 250
_VERSION_W = 320


class AboutCard(QtWidgets.QFrame):
    def __init__(
        self,
        rows: list[dict],
        blocked: str,
        state: dict,
        on_apply: Callable[[list[str], list[str]], None],
        on_back: Callable[[], None],
        compact: bool = False,
    ):
        super().__init__()
        self.setObjectName("modalCard")
        # Wider than other cards: three columns to line up.
        self.setMinimumWidth(560)
        self._rows = rows
        self._blocked = blocked
        self._state = state
        self._on_apply = on_apply
        self._online = network.is_enabled()
        self._proc: QtCore.QProcess | None = None

        title = QtWidgets.QLabel("About")
        title.setObjectName("modalTitle")

        self.status_lbl = QtWidgets.QLabel()
        self.status_lbl.setObjectName("dialogNote")
        self.status_lbl.setWordWrap(True)

        self._grid = QtWidgets.QGridLayout()
        # Right margin keeps Update buttons off the scrollbar.
        self._grid.setContentsMargins(0, 0, 8, 0)
        self._grid.setHorizontalSpacing(12)
        # Rows of Update buttons, so a thumb needs a gap even where height is tight.
        self._grid.setVerticalSpacing(6 if compact else 8)
        # Slack goes to versions, labels hug their text.
        self._grid.setColumnStretch(1, 1)

        # Every sensor plus the stack and kernel outgrows the panel, so the list scrolls.
        body = QtWidgets.QWidget()
        body.setLayout(self._grid)
        self._scroll = QtWidgets.QScrollArea()
        self._scroll.setWidget(body)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Tab belongs to the buttons, the list scrolls by drag and wheel.
        self._scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        kinetic_scroll(self._scroll.viewport())

        self.check_btn = QtWidgets.QPushButton("Check for updates")
        self.check_btn.clicked.connect(self._check)
        self.check_btn.setEnabled(self._online)
        # One boot installs the lot, so sending them together saves a second reboot.
        self.all_btn = QtWidgets.QPushButton("Update all")
        self.all_btn.clicked.connect(self._apply_all)
        # Back, not Close: the setting that gates a check sits one card behind.
        back_btn = QtWidgets.QPushButton("Back")
        back_btn.clicked.connect(on_back)
        # TabFocus keeps a press from leaving a focus ring, and keeps the ring off
        # the next button when Check disables itself mid-check.
        for btn in (self.check_btn, self.all_btn, back_btn):
            btn.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        # Every other button here reboots the box, so Enter lands on Back.
        self.primary_button = back_btn
        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.check_btn)
        buttons.addWidget(self.all_btn)
        buttons.addStretch(1)
        buttons.addWidget(back_btn)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(*((18, 10, 18, 10) if compact else (22, 20, 22, 18)))
        lay.setSpacing(6 if compact else 14)
        lay.addWidget(title)
        lay.addWidget(self._scroll, 1)
        lay.addWidget(self.status_lbl)
        lay.addWidget(hline())
        lay.addLayout(buttons)

        self._rebuild()

    def _rebuild(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        pending = updater.pending_ids(self._state)
        # A blocked box surveys like any other, so it shows what waits upstream and
        # leaves the operator to install it from outside camlab.
        offers = not self._blocked
        # Only worth its own button when it saves a reboot.
        self.all_btn.setVisible(offers and len(pending) > 1)
        surveyed = {c["id"]: c for c in self._state.get("components") or []}

        for row, item in enumerate(self._rows):
            waiting = item["id"] in pending
            installed, available = item["installed"], ""
            if waiting:
                # Survey carries the version it moves to, which an inventory row cannot.
                installed, available = updater.component_summary(surveyed[item["id"]])
            version = QtWidgets.QLabel(f"{installed} \u2192 {available}" if waiting else installed)
            version.setObjectName("modalText" if item["updatable"] else "dialogNote")
            version.setWordWrap(True)
            version.setMinimumWidth(_VERSION_MIN)
            version.setMaximumWidth(_VERSION_W)
            self._grid.addWidget(QtWidgets.QLabel(item["label"]), row, 0)
            self._grid.addWidget(version, row, 1)
            if waiting and offers:
                button = QtWidgets.QPushButton("Update")
                button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
                button.clicked.connect(
                    lambda _checked, i=item: self._on_apply([i["id"]], [i["label"]])
                )
                self._grid.addWidget(button, row, 2)
        self._set_status(self._status_text())

    def _set_status(self, text: str) -> None:
        """Nothing to report hides the label, an empty one would still hold a line."""
        self.status_lbl.setText(text)
        self.status_lbl.setVisible(bool(text))

    def _apply_all(self) -> None:
        pending = updater.pending_ids(self._state)
        labels = {c["id"]: c["label"] for c in self._state.get("components") or []}
        self._on_apply(pending, [labels.get(i, i) for i in pending])

    def _status_text(self) -> str:
        """One line, whatever stands most in the way. Nothing in the way stays quiet."""
        if self._blocked:
            return f"Updates off: {self._blocked}"
        if not self._online:
            return "Checking needs networking"
        # Only a check can tell, so an unchecked box does not claim to be current.
        if self._state.get("checked") and not updater.pending_ids(self._state):
            return "Up to date"
        if (self._state.get("last_run") or {}).get("error"):
            # Still pending next to its Update button. update.log carries apt's words.
            return "Last update failed"
        return ""

    def _check(self) -> None:
        if self._proc is not None:
            return
        # Label held still, the footer says what is happening.
        self.check_btn.setEnabled(False)
        self._set_status("Checking\u2026")
        self._proc = QtCore.QProcess(self)
        self._proc.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_error)
        QtCore.QTimer.singleShot(_CHECK_TIMEOUT_MS, self._timeout)
        cmd = updater.check_command()
        self._proc.start(cmd[0], cmd[1:])

    def _timeout(self) -> None:
        if self._proc is not None and self._proc.state() != QtCore.QProcess.ProcessState.NotRunning:
            self._proc.kill()

    def _on_error(self, error) -> None:
        # Only a start failure skips finished, the rest arrive there with an exit code.
        if error == QtCore.QProcess.ProcessError.FailedToStart:
            self._check_done(f"{updater.UPDATE_BIN} did not start")

    def _on_finished(self, code: int, _status) -> None:
        output = ""
        if self._proc is not None:
            output = bytes(self._proc.readAll()).decode(errors="replace").strip()
            self._proc.deleteLater()
        self._proc = None
        self._state = updater.read_state()
        self._blocked = updater.update_path()
        self._rebuild()
        error = ""
        if code != 0:
            error = output.splitlines()[-1] if output else f"check exited {code}"
        self._check_done(error)

    def _check_done(self, error: str = "") -> None:
        self._proc = None
        self.check_btn.setEnabled(self._online)
        if error:
            # Card stays short, the log panel carries apt's reason.
            log.error("update check failed: %s", error.removeprefix("error: "))
            self._set_status("Check failed")

    def hideEvent(self, event) -> None:
        # Dismissing the modal deletes this card, do not leave apt running behind it.
        if self._proc is not None:
            self._proc.kill()
        super().hideEvent(event)
