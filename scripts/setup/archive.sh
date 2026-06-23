#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# Add the Kurokesu APT archive (signed) so the epoch-forced libcamera/rpicam-apps
# fork (+krks) and sensor packages install and stay preferred over stock.
# Safe to re-run. Requires sudo.
#
# Usage: sudo scripts/setup/archive.sh

set -euo pipefail

CAMTEST_TAG="archive"

# shellcheck source=../common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

for arg in "$@"; do
    case "$arg" in
        -h|--help) help_text; exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

require_root

KEY_URL="https://apt.kurokesu.com/kurokesu-archive-keyring.gpg"
KEYRING="/etc/apt/keyrings/kurokesu-archive-keyring.gpg"
SOURCES="/etc/apt/sources.list.d/kurokesu.sources"

header "Adding Kurokesu APT archive"

install -d -m 0755 /etc/apt/keyrings
log "Fetching archive key -> $KEYRING"
curl -fsSL "$KEY_URL" -o "$KEYRING"
chmod 0644 "$KEYRING"

log "Writing $SOURCES"
cat > "$SOURCES" <<EOF
Types: deb
URIs: https://apt.kurokesu.com
Suites: trixie
Components: main
Architectures: arm64
Signed-By: $KEYRING
EOF

log "apt-get update"
apt-get update

log "Done. Kurokesu archive ready."
