#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# Boot-time tuning (Phase 5). Intended tweaks: quiet/loglevel + plymouth, disable
# Bluetooth, mask NetworkManager-wait-online, and instrument boot-to-preview.
# NOT yet implemented - this is a placeholder so install.sh ordering and the repo
# layout match the spec. Editing cmdline.txt/config.txt is deferred until the
# boot-tuning phase (see plan: boot-overlayroot).
# Requires sudo.
#
# Usage: sudo scripts/setup/boot.sh

set -euo pipefail

CAMTEST_TAG="boot"

# shellcheck source=../common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

for arg in "$@"; do
    case "$arg" in
        -h|--help) help_text; exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

require_root

header "Boot tuning (Phase 5 - not yet implemented)"
warn "boot.sh is a placeholder. Planned: quiet boot, disable BT,"
warn "mask wait-online, boot-to-preview instrumentation. No changes made."
exit 0
