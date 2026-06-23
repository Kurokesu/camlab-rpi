#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# Privileged apply shim - rendered to /usr/local/bin/camtest-apply by
# scripts/setup/config.sh (CAMTEST_REPO_DIR substituted). This is the ONLY
# command the GUI user is allowed to run as root for config writes (see
# deploy/camtest-sudoers). It does nothing but the managed-block rewrite.

set -euo pipefail
cd "CAMTEST_REPO_DIR" || exit 1
exec /usr/bin/python3 -m camtest.config_manager "$@"
