#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Privileged apply shim - rendered to /usr/local/bin/camlab-apply by
# scripts/setup/config.sh (CAMLAB_REPO_DIR substituted). This is the ONLY
# command the GUI user is allowed to run as root for config writes (see
# deploy/camlab-sudoers). It does nothing but the managed-block rewrite.

set -euo pipefail
cd "CAMLAB_REPO_DIR" || exit 1
exec /usr/bin/python3 -m camlab.config_manager "$@"
