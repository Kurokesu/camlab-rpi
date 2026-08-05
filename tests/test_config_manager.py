# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""ConfigManager block parsing, composition, validation and port arbitration."""

from __future__ import annotations

from pathlib import Path

import pytest

from camlab import config_manager
from camlab.config_manager import ConfigError, ConfigManager

PANEL = "vc4-kms-dsi-7inch"


@pytest.fixture(autouse=True)
def isolated_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drm_root: Path) -> None:
    """Absent device-tree model reads as Pi 5 (not a Compute Module)."""
    monkeypatch.setattr(config_manager, "MODEL_PATH", tmp_path / "model")


@pytest.fixture
def cm(tmp_path: Path) -> ConfigManager:
    overlays = tmp_path / "overlays"
    overlays.mkdir()
    for token in ("ar0234", "ar0822", PANEL):
        (overlays / f"{token}.dtbo").touch()
    return ConfigManager(config_path=tmp_path / "config.txt", overlays_dir=overlays)


class TestCompose:
    def test_cam1_omits_port_param(self):
        assert ConfigManager.compose_dtoverlay("ar0234", "cam1", []) == "dtoverlay=ar0234"

    def test_cam0_appends_port(self):
        assert ConfigManager.compose_dtoverlay("ar0234", "cam0", []) == "dtoverlay=ar0234,cam0"

    def test_options_follow_port(self):
        line = ConfigManager.compose_dtoverlay("ar0822", "cam0", ["4lane", "mono"])
        assert line == "dtoverlay=ar0822,cam0,4lane,mono"

    def test_invalid_port_rejected(self):
        with pytest.raises(ConfigError):
            ConfigManager.compose_dtoverlay("ar0234", "dsi0", [])

    def test_display_overlay_takes_free_connector(self):
        # Camera on cam0 leaves DISP1 (overlay default), cam1 leaves DISP0.
        assert ConfigManager.compose_display_overlay(PANEL, "cam0") == PANEL
        assert ConfigManager.compose_display_overlay(PANEL, "cam1") == f"{PANEL},dsi0"


class TestValidation:
    @pytest.mark.parametrize("bad", ["", "a b", "a\nb", "x#y", "dt,ovl"])
    def test_overlay_charset(self, bad):
        with pytest.raises(ConfigError):
            ConfigManager.compose_dtoverlay(bad, "cam1", [])

    def test_option_charset(self):
        with pytest.raises(ConfigError):
            ConfigManager.compose_dtoverlay("ar0234", "cam1", ["4lane\nboot_delay=10"])

    def test_display_rewrite_rejects_injection(self, cm):
        with pytest.raises(ConfigError):
            cm._rewrite_display_in_place(f"{PANEL}\ndtoverlay=evil")
        assert not cm.config_path.exists()


class TestCameraBlock:
    def test_rewrite_creates_block(self, cm):
        cm._rewrite_in_place("ar0234", "cam1", [])
        assert cm.get_current() == {
            "overlay": "ar0234",
            "port": "cam1",
            "options": [],
            "camera_auto_detect": "0",
            "present": True,
        }

    def test_rewrite_preserves_user_content(self, cm):
        cm.config_path.write_text("arm_boost=1\n")
        cm._rewrite_in_place("ar0822", "cam0", ["4lane"])
        text = cm.config_path.read_text()
        assert text.startswith("arm_boost=1\n")
        cur = cm.get_current()
        assert (cur["overlay"], cur["port"], cur["options"]) == ("ar0822", "cam0", ["4lane"])

    def test_rewrite_replaces_existing_block(self, cm):
        cm._rewrite_in_place("ar0234", "cam1", [])
        cm._rewrite_in_place("ar0822", "cam0", [])
        assert cm.config_path.read_text().count(config_manager.BEGIN) == 1
        assert cm.get_current()["overlay"] == "ar0822"

    def test_missing_dtbo_rejected(self, cm):
        with pytest.raises(ConfigError):
            cm._rewrite_in_place("imx999", "cam1", [])

    def test_absent_block_parses_defaults(self, cm):
        cur = cm.get_current()
        assert cur["present"] is False
        assert cur["port"] == "cam1"


class TestDisplayBlock:
    def test_write_and_parse_dsi0(self, cm):
        cm._rewrite_display_in_place(f"{PANEL},dsi0")
        disp = cm.get_current_display()
        assert disp["present"] is True
        assert disp["overlay"] == f"{PANEL},dsi0"
        assert disp["dsi0"] is True
        assert disp["port_blocked"] == "cam0"
        assert "display_auto_detect=0" in cm.config_path.read_text()

    def test_default_connector_blocks_cam1(self, cm):
        cm._rewrite_display_in_place(PANEL)
        disp = cm.get_current_display()
        assert disp["dsi0"] is False
        assert disp["port_blocked"] == "cam1"

    def test_clear_removes_block_only(self, cm):
        cm.config_path.write_text("arm_boost=1\n")
        cm._rewrite_display_in_place(PANEL)
        cm._rewrite_display_in_place(None)
        assert cm.get_current_display()["present"] is False
        assert "arm_boost=1" in cm.config_path.read_text()

    def test_blocks_coexist(self, cm):
        cm._rewrite_display_in_place(PANEL)
        cm._rewrite_in_place("ar0234", "cam0", [])
        assert cm.get_current()["overlay"] == "ar0234"
        assert cm.get_current_display()["overlay"] == PANEL


class TestPortArbitration:
    def test_camera_rejected_on_claimed_port(self, cm):
        cm._rewrite_display_in_place(PANEL)  # claims cam1 next boot
        with pytest.raises(ConfigError):
            cm._rewrite_in_place("ar0234", "cam1", [])

    def test_display_block_wins_over_live_drm(self, cm, fake_drm):
        # Pending change: block says cam1 while DRM still shows DSI-1 (cam0).
        fake_drm({"DSI-1": "connected"})
        cm._rewrite_display_in_place(PANEL)
        assert cm.blocked_ports_next_boot() == {"cam1"}
        assert cm.free_port() == "cam0"

    def test_live_drm_without_block(self, cm, fake_drm):
        fake_drm({"DSI-1": "connected"})
        assert cm.blocked_ports_next_boot() == {"cam0"}
        assert cm.free_port() == "cam1"

    def test_compute_module_ignores_live_drm(self, cm, fake_drm):
        # CM carrier DSI wiring is not tied to CSI ports like the Pi 5 pairs.
        fake_drm({"DSI-1": "connected"})
        config_manager.MODEL_PATH.write_text("Raspberry Pi Compute Module 5 Rev 1.0")
        assert cm.blocked_ports_next_boot() == set()

    def test_no_free_port_raises(self, cm, fake_drm):
        fake_drm({"DSI-1": "connected", "DSI-2": "connected"})
        with pytest.raises(ConfigError):
            cm.free_port()
