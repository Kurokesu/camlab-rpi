# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later

"""Every module must import.

Class bodies run at import: profiles, chip specs and registries are built there,
so a bad dataclass field order or constructor call fails here and nowhere else.
Compiling and linting both miss it.
"""

from __future__ import annotations

import importlib
import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest

import camlab

# Absent on a runner, present on a Pi. Any other missing module is a real break.
HARDWARE_DEPS = {"picamera2", "libcamera", "PyQt6", "OpenGL"}

MODULES = sorted(m.name for m in pkgutil.walk_packages(camlab.__path__, "camlab."))

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_walk_reaches_subpackages():
    # Empty parametrize reads as a skip, not a failure, so assert the walk worked.
    assert MODULES, "no camlab modules discovered"
    assert any(name.startswith("camlab.gui.") for name in MODULES), MODULES


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str):
    try:
        importlib.import_module(name)
    except ModuleNotFoundError as exc:
        missing = (exc.name or "").split(".")[0]
        if missing not in HARDWARE_DEPS:
            raise
        pytest.skip(f"{missing} not installed")


@pytest.mark.parametrize("name", ["camlab.drm", "camlab.config_manager"])
def test_privileged_cli_imports_no_qt(name: str):
    """Qt on camlab-apply import path breaks the only privileged config write."""
    # This interpreter has Qt loaded by other tests, only a fresh one can answer
    code = f"import {name}, sys; assert 'PyQt6' not in sys.modules"
    probe = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, check=False)
    assert probe.returncode == 0
