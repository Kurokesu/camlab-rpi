# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Display-only twin of chrome."""

from __future__ import annotations

from collections.abc import Callable

from ..qt import Qt, QtCore, QtWidgets
from ..settings import MonitorState
from .status_strip import StatusStrip
from .style import UiProfile, build_stylesheet
from .viewfinder_area import ViewfinderArea


class MirrorView(QtWidgets.QWidget):
    def __init__(
        self,
        engine,
        focus_sampler,
        monitor_state: Callable[[], MonitorState],
        profile: UiProfile,
        parent: QtWidgets.QWidget | None = None,
        screen_rect: QtCore.QRect | None = None,
    ):
        super().__init__(parent)
        self._engine = engine
        self._monitor_state = monitor_state
        self._assists: MonitorState | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.status = StatusStrip()
        root.addWidget(self.status)

        live = engine.make_mirror() if engine.picam2 is not None else None
        self.viewfinder_area = ViewfinderArea(engine, live=live)
        root.addWidget(self.viewfinder_area, 1)
        focus_sampler.sample.connect(lambda s: self.viewfinder_area.update_focus_map(s.heat))

        self.apply_profile(profile, screen_rect)
        self.update_status(engine.telemetry)

    def apply_profile(self, profile: UiProfile, screen_rect: QtCore.QRect | None) -> None:
        """Re-skin for display mirror now sits on.

        Pane swap reuses one mirror, since second make_mirror brings up GL context that
        resets texture stream live view shares.
        """
        self._profile = profile
        self._screen_rect = screen_rect
        self.setStyleSheet(build_stylesheet(profile))
        self.status.set_compact(profile.compact)
        self.viewfinder_area.apply_profile(profile)

    @property
    def profile(self) -> UiProfile:
        """Density mirror is skinned for."""
        return self._profile

    @property
    def screen_rect(self) -> QtCore.QRect | None:
        """Screen rect mirror sits on."""
        return self._screen_rect

    def update_status(self, t) -> None:
        """Chrome pushes its snapshot, so both displays never sit on different frames."""
        md = t.metadata or {}
        self.status.set_telemetry(
            t.frame,
            t.fps if t.fps > 0 else None,
            md.get("ExposureTime"),
            md.get("AnalogueGain"),
            md.get("DigitalGain"),
        )
        self.status.set_temperature(md.get("SensorTemperature"))
        self._sync_assists()
        if self._engine.latest_histogram is not None:
            self.viewfinder_area.update_histogram(self._engine.latest_histogram)

    def _sync_assists(self) -> None:
        """Both displays draw assists picked on chrome's monitor sheet."""
        state = self._monitor_state()
        if state == self._assists:
            return
        self._assists = state
        self.viewfinder_area.set_assists(state.peaking, state.zebra, state.zebra_threshold)
        self.viewfinder_area.set_histogram_enabled(state.histogram)
        self.viewfinder_area.set_focus_map_enabled(state.focus_map)
