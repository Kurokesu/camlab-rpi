#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Update path: privilege shim (camlab-update) for the GUI.
# Safe to re-run. Requires sudo.
#
# Usage: sudo scripts/setup/update.sh

set -euo pipefail

# shellcheck disable=SC2034  # log tag read by common.sh
CAMLAB_TAG="update"

# shellcheck source=../common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

for arg in "$@"; do
    case "$arg" in
        -h|--help) help_text; exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

require_root
REPO_DIR="$(resolve_repo_dir)"

header "Configuring the update path"

log "Installing /usr/local/bin/camlab-update"
sed -e "s|CAMLAB_REPO_DIR|$REPO_DIR|g" \
    "$REPO_DIR/scripts/camlab-update.sh" > /usr/local/bin/camlab-update
chmod 0755 /usr/local/bin/camlab-update

log "Done."
