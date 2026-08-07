# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Updates card: one row per component, update them one at a time.

Rows come from the last check recorded in update.json, so opening costs a file
read. Check runs the privileged shim through QProcess, an apt refresh over a
slow link would otherwise freeze the kiosk for a minute.
"""

from __future__ import annotations

from collections.abc import Callable

from .. import network, updater
from ..qt import QtCore, QtWidgets
from .widgets import hline

_CHECK_TIMEOUT_MS = 120_000


class UpdatesCard(QtWidgets.QFrame):
    def __init__(
        self,
        state: dict,
        on_apply: Callable[[list[str], list[str]], None],
        on_close: Callable[[], None],
        compact: bool = False,
    ):
        super().__init__()
        self.setObjectName("modalCard")
        self.setMinimumWidth(440)
        self._state = state
        self._on_apply = on_apply
        self._compact = compact
        self._proc: QtCore.QProcess | None = None

        title = QtWidgets.QLabel("Updates")
        title.setObjectName("modalTitle")

        self.status_lbl = QtWidgets.QLabel()
        self.status_lbl.setObjectName("dialogNote")
        self.status_lbl.setWordWrap(True)

        self._grid = QtWidgets.QGridLayout()
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(2 if compact else 8)
        self._grid.setColumnStretch(0, 1)

        self.check_btn = QtWidgets.QPushButton("Check")
        self.check_btn.clicked.connect(self._check)
        # One boot installs the lot, so sending them together saves a second reboot.
        self.all_btn = QtWidgets.QPushButton("Update all")
        self.all_btn.clicked.connect(self._apply_all)
        close_btn = QtWidgets.QPushButton("Close")
        close_btn.clicked.connect(on_close)
        # Every other button here reboots the box, so Enter lands on Close.
        self.primary_button = close_btn
        buttons = QtWidgets.QHBoxLayout()
        buttons.addWidget(self.check_btn)
        buttons.addWidget(self.all_btn)
        buttons.addStretch(1)
        buttons.addWidget(close_btn)

        lay = QtWidgets.QVBoxLayout(self)
        # Five rows plus chrome, so the touch panel needs every pixel it can keep.
        lay.setContentsMargins(*((18, 12, 18, 12) if compact else (22, 20, 22, 18)))
        lay.setSpacing(8 if compact else 14)
        lay.addWidget(title)
        lay.addLayout(self._grid)
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
        # Only worth its own button when it saves a reboot.
        self.all_btn.setVisible(len(pending) > 1)

        blocked = self._state.get("blocked")
        if blocked:
            self._grid.addWidget(self._note(f"Updates are not available here: {blocked}."), 0, 0)
            self.check_btn.setEnabled(False)
            self.all_btn.setVisible(False)
            self.status_lbl.setText("")
            return

        components = self._state.get("components") or []
        if not components:
            self._grid.addWidget(self._note("Nothing checked yet. Check looks for updates."), 0, 0)
        for row, component in enumerate(components):
            installed, available = updater.component_summary(component)
            label = QtWidgets.QLabel(component["label"])
            version = QtWidgets.QLabel(
                f"{installed} \u2192 {available}" if available else installed
            )
            version.setObjectName("modalText" if available else "dialogNote")
            button = QtWidgets.QPushButton("Update")
            button.setEnabled(bool(available))
            button.clicked.connect(
                lambda _checked, c=component: self._on_apply([c["id"]], [c["label"]])
            )
            self._grid.addWidget(label, row, 0)
            self._grid.addWidget(version, row, 1)
            self._grid.addWidget(button, row, 2)
        self.status_lbl.setText(self._status_text())

    def _apply_all(self) -> None:
        pending = updater.pending_ids(self._state)
        labels = {c["id"]: c["label"] for c in self._state.get("components") or []}
        self._on_apply(pending, [labels.get(i, i) for i in pending])

    @staticmethod
    def _note(text: str) -> QtWidgets.QLabel:
        note = QtWidgets.QLabel(text)
        note.setObjectName("dialogNote")
        note.setWordWrap(True)
        note.setMaximumWidth(420)
        return note

    def _status_text(self) -> str:
        parts = []
        checked = self._state.get("checked") or ""
        if checked:
            stamp = checked[5:16] if self._compact else checked[:16]
            parts.append(f"Checked {stamp.replace('T', ' ')} UTC.")
        if checked and not updater.pending_ids(self._state):
            parts.append("Everything is up to date.")
        if (self._state.get("last_run") or {}).get("error"):
            # Apt's own wording is unreadable here, update.log keeps it.
            parts.append("Last update failed.")
        return " ".join(parts)

    def _check(self) -> None:
        if self._proc is not None:
            return
        self.check_btn.setEnabled(False)
        self.check_btn.setText("Checking")
        self.status_lbl.setText("Refreshing the archive index.")
        try:
            if not network.is_enabled():
                network.set_enabled(True)
                self.status_lbl.setText("Networking turned on, refreshing the archive index.")
        except Exception as exc:  # noqa: BLE001 a failed toggle is the check's problem to report
            self._check_done(f"networking would not come up: {exc}")
            return
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
        self._rebuild()
        error = ""
        if code != 0:
            error = output.splitlines()[-1] if output else f"check exited {code}"
        self._check_done(error)

    def _check_done(self, error: str = "") -> None:
        self._proc = None
        self.check_btn.setEnabled(True)
        self.check_btn.setText("Check")
        if error:
            self.status_lbl.setText(f"Check failed: {error.removeprefix('error: ')}")

    def hideEvent(self, event) -> None:
        # Dismissing the modal deletes this card, do not leave apt running behind it.
        if self._proc is not None:
            self._proc.kill()
        super().hideEvent(event)
