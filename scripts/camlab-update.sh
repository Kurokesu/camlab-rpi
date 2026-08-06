#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Privileged update shim - rendered to /usr/local/bin/camlab-update by
# scripts/setup/update.sh (CAMLAB_REPO_DIR substituted). Sudoers grants the GUI
# user check and apply only (deploy/camlab-sudoers), and camlab.updater resolves
# component ids itself, so no caller ever names a package.

set -euo pipefail
cd "CAMLAB_REPO_DIR" || exit 1
exec /usr/bin/python3 -m camlab.updater "$@"
