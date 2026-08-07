#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Install the camlab-apply shim and the sudoers allow-list the GUI runs through.
# Converged on an update boot, so a release that adds a rule reaches old installs.
# Safe to re-run. Requires sudo.
#
# Usage: sudo scripts/setup/shims.sh

set -euo pipefail

# shellcheck disable=SC2034  # log tag read by common.sh
CAMLAB_TAG="shims"

# shellcheck source=../common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

for arg in "$@"; do
    case "$arg" in
        -h|--help) help_text; exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

require_root

# Granting root rights to the wrong account is worse than granting none.
[ "$CAMLAB_USER" != "root" ] || die "Cannot tell who owns the kiosk. Re-run under sudo from that account."
save_camlab_user

REPO_DIR="$(resolve_repo_dir)"

header "Installing privilege shims for $CAMLAB_USER"

log "Installing /usr/local/bin/camlab-apply"
sed -e "s|CAMLAB_REPO_DIR|$REPO_DIR|g" \
    "$REPO_DIR/scripts/camlab-apply.sh" > /usr/local/bin/camlab-apply
chmod 0755 /usr/local/bin/camlab-apply

log "Installing /etc/sudoers.d/camlab"
tmp_sudoers="$(mktemp)"
sed -e "s|CAMLAB_USER|$CAMLAB_USER|g" \
    "$REPO_DIR/deploy/camlab-sudoers" > "$tmp_sudoers"
if visudo -c -f "$tmp_sudoers" >/dev/null; then
    install -m 0440 "$tmp_sudoers" /etc/sudoers.d/camlab
    rm -f "$tmp_sudoers"
    log "sudoers validated and installed"
else
    rm -f "$tmp_sudoers"
    die "generated sudoers failed visudo validation; not installing"
fi

log "Done."
