#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Configure camera overlay + GUI privileges:
#   1. Write camlab managed block in /boot/firmware/config.txt
#      with default sensor/port (via camlab.config_manager).
#   2. Install scoped privilege shim: /usr/local/bin/camlab-apply +
#      /etc/sudoers.d/camlab (validated with visudo).
# Safe to re-run. Requires sudo. Reboot needed for overlay changes.
#
# Usage:
#   sudo scripts/setup/config.sh           # default ar0234 on a free CSI port
#   sudo scripts/setup/config.sh --sensor ar0822 --options 4lane

set -euo pipefail

# shellcheck disable=SC2034  # log tag read by common.sh
CAMLAB_TAG="config"

# shellcheck source=../common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

SENSOR="ar0234"
OPTIONS=("4lane")
OPTIONS_GIVEN=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        --sensor) SENSOR="$2"; shift 2 ;;
        --options) [ "$OPTIONS_GIVEN" -eq 1 ] || OPTIONS=(); OPTIONS+=("$2"); OPTIONS_GIVEN=1; shift 2 ;;
        --no-options) OPTIONS=(); OPTIONS_GIVEN=1; shift ;;
        -h|--help) help_text; exit 0 ;;
        *) die "Unknown argument: $1" ;;
    esac
done

require_root
REPO_DIR="$(resolve_repo_dir)"

# Prefer cam1 (overlay default). Fall back to cam0 when a DSI panel claims it
# (Pi 5 auto-detect via live DRM, or a display block written earlier).
PORT="$(
    cd "$REPO_DIR" && python3 - <<'PY'
from camlab.config_manager import ConfigManager

blocked = ConfigManager().blocked_ports_next_boot()
for port in ("cam1", "cam0"):
    if port not in blocked:
        print(port)
        break
else:
    raise SystemExit("no free CSI port: display claims both connectors")
PY
)"

header "Configuring overlay: $SENSOR on $PORT (options: ${OPTIONS[*]:-none})"

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
