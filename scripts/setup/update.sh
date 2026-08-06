#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Update path: privilege shim (camlab-update) and the update boot unit.
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

UNIT="camlab-update.service"

# Rendered aside, so an update boot re-running this cannot truncate its own shim.
log "Installing /usr/local/bin/camlab-update"
tmp_shim="$(mktemp)"
sed -e "s|CAMLAB_REPO_DIR|$REPO_DIR|g" \
    "$REPO_DIR/scripts/camlab-update.sh" > "$tmp_shim"
install -m 0755 "$tmp_shim" /usr/local/bin/camlab-update
rm -f "$tmp_shim"

log "Installing /etc/systemd/system/$UNIT"
install -m 0644 "$REPO_DIR/deploy/$UNIT" "/etc/systemd/system/$UNIT"
systemctl daemon-reload
systemctl enable "$UNIT" >/dev/null
log "enabled $UNIT (inert until camlab-update apply arms a plan)"

log "Done."
