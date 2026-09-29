# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Chrome follows input device last used.

Layout gap holds mouse on monitor and touch matrix holds touch on DSI.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from ..qt import QtCore, QtGui, QtWidgets

log = logging.getLogger(__name__)


class DisplayClaim(QtCore.QObject):
    """Press that moves chrome is swallowed.

    Touch arrives as press carrying touchscreen, so one event type covers both devices.
    """

    def __init__(self, app: QtWidgets.QApplication, claim: Callable[[bool], bool]):
        super().__init__(app)
        self._claim = claim
        self._err_logged = False
        app.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:
        # Exception escaping Qt virtual would abort PyQt
        try:
            if event.type() == QtCore.QEvent.Type.MouseButtonPress:
                dev = event.pointingDevice()
                if dev is not None:
                    return self._claim(dev.type() == QtGui.QInputDevice.DeviceType.TouchScreen)
        except Exception:  # claim is a convenience
            if not self._err_logged:
                self._err_logged = True
                log.exception("display claim filter failed (once)")
        return False
