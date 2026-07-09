#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# Configure camera overlay + GUI privileges:
#   1. Set CSI port (cam0/cam1), default cam1.
#   2. Write camlab managed block in /boot/firmware/config.txt for chosen
#      sensor/port (via camlab.config_manager).
#   3. Install scoped privilege shim: /usr/local/bin/camlab-apply +
#      /etc/sudoers.d/camlab (validated with visudo).
# Safe to re-run. Requires sudo. Reboot needed for overlay changes.
#
# Usage:
#   sudo scripts/setup/config.sh                    # port defaults to cam1
#   sudo scripts/setup/config.sh --port cam0        # override CSI port
#   sudo scripts/setup/config.sh --sensor ar0822 --port cam1 --options 4lane

set -euo pipefail

# shellcheck disable=SC2034  # log tag read by common.sh
CAMLAB_TAG="config"

# shellcheck source=../common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

SENSOR="ar0234"
PORT="cam1"
OPTIONS=("4lane")
OPTIONS_GIVEN=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --sensor) SENSOR="$2"; shift 2 ;;
        --port) PORT="$2"; shift 2 ;;
        --options) [ "$OPTIONS_GIVEN" -eq 1 ] || OPTIONS=(); OPTIONS+=("$2"); OPTIONS_GIVEN=1; shift 2 ;;
        --no-options) OPTIONS=(); OPTIONS_GIVEN=1; shift ;;
        -h|--help) help_text; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

require_root
REPO_DIR="$(resolve_repo_dir)"

[ "$PORT" = "cam0" ] || [ "$PORT" = "cam1" ] || die "invalid port '$PORT'"

header "Configuring overlay: $SENSOR on $PORT (options: ${OPTIONS[*]:-none})"

# Write the managed block in config.txt for the chosen sensor/port.
opt_args=()
for o in "${OPTIONS[@]:-}"; do [ -n "$o" ] && opt_args+=(--options "$o"); done
( cd "$REPO_DIR" && python3 -m camlab.config_manager set \
    --overlay "$SENSOR" --port "$PORT" "${opt_args[@]}" )

# Install the scoped privilege shim + sudoers rule.
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
