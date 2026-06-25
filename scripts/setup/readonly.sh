#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# Overlay (read-only) root (Phase 5). Makes the rootfs tolerant of yanked power -
# the field failure mode for a bench tool. NOT yet implemented. Targets the eMMC
# rootfs (the shipping medium) and always runs LAST in install.sh (and skippable
# via install.sh --no-readonly).
# Requires sudo.
#
# Usage: sudo scripts/setup/readonly.sh

set -euo pipefail

CAMTEST_TAG="readonly"

# shellcheck source=../common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

for arg in "$@"; do
    case "$arg" in
        -h|--help) help_text; exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

require_root

header "Overlay root (Phase 5 - not yet implemented)"
warn "readonly.sh is a placeholder. Planned: overlayfs read-only root with a"
warn "writable overlay, plus camtestctl rw/ro toggles. No changes made."
warn "Must mount a writable data partition at /var/lib/camtest (the service's"
warn "StateDirectory) excluded from the overlay, so the persisted mode/fps"
warn "selection survives reboots under the read-only root."
exit 0
