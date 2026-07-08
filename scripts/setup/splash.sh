#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# Boot/shutdown splash: a Plymouth theme (logo + loader bar) that runs until
# Cage starts, static logo on shutdown. Also silences the console: no rainbow
# splash, no tty text, no cursor. Artwork lives in deploy/splash/.
# Safe to re-run. Requires sudo. Reboot to apply.
#
# Usage:
#   sudo scripts/setup/splash.sh            # install + activate
#   sudo scripts/setup/splash.sh --revert   # undo everything
#   sudo scripts/setup/splash.sh --help

set -euo pipefail

# shellcheck disable=SC2034  # log tag read by common.sh
CAMLAB_TAG="splash"

# shellcheck source=../common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

REVERT=0
for arg in "$@"; do
    case "$arg" in
        --revert) REVERT=1 ;;
        -h|--help) help_text; exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

require_root

REPO_DIR="$(resolve_repo_dir)"
SPLASH_SRC="$REPO_DIR/deploy/splash"
THEME_DST="/usr/share/plymouth/themes/camlab"

FW_DIR="${CAMLAB_FW_DIR:-/boot/firmware}"
CONFIG_TXT="$FW_DIR/config.txt"
CMDLINE_TXT="$FW_DIR/cmdline.txt"

QUIET_DROPIN="/etc/systemd/system.conf.d/camlab-splash.conf"

# Cmdline token sets. Add/remove one token at a time (never rewrite the whole
# line) so the overlayroot token managed by readonly.sh/camlabctl survives.
# ignore-serial-consoles is load-bearing: without it Plymouth attaches to the
# serial console (last console= entry) and the HDMI panel stays black.
CMDLINE_ADD=(
    splash
    quiet
    plymouth.ignore-serial-consoles
    vt.global_cursor_default=0
)
CMDLINE_REMOVE=(
    console=tty1
)

BEGIN="# >>> camlab splash (do not edit) >>>"
END="# <<< camlab splash <<<"

_atomic_write() {
    local path="$1" content="$2" tmp
    tmp="$(mktemp "${path}.camlab-XXXXXX")"
    printf '%s' "$content" > "$tmp"
    if [ -f "$path" ]; then chmod --reference="$path" "$tmp" 2>/dev/null || true; fi
    mv -f "$tmp" "$path"
}

_cmdline_has() { grep -qE "(^| )$1( |\$)" "$CMDLINE_TXT"; }

_cmdline_add() {
    _cmdline_has "$1" && return 0
    sed -i "s/[[:space:]]*\$/ $1/" "$CMDLINE_TXT"
}

_cmdline_remove() {
    sed -i -E "s/(^| )$1( |\$)/\1/; s/[[:space:]]+\$//" "$CMDLINE_TXT"
}

stage_packages() {
    if [ "$REVERT" -eq 1 ]; then
        log "leaving splash packages installed (harmless, removal is manual)"
        return
    fi
    log "Stage: packages"
    DEBIAN_FRONTEND=noninteractive apt_get install -y --no-install-recommends \
        plymouth plymouth-themes >/dev/null
}

stage_cmdline() {
    [ -f "$CMDLINE_TXT" ] || { warn "$CMDLINE_TXT missing, skipping"; return; }
    local t
    if [ "$REVERT" -eq 1 ]; then
        for t in "${CMDLINE_ADD[@]}"; do _cmdline_remove "$t"; done
        _cmdline_add "console=tty1"
        log "cmdline.txt: splash tokens removed, console=tty1 restored"
        return
    fi
    log "Stage: cmdline.txt"
    for t in "${CMDLINE_REMOVE[@]}"; do _cmdline_remove "$t"; done
    for t in "${CMDLINE_ADD[@]}"; do _cmdline_add "$t"; done
}

stage_config() {
    [ -f "$CONFIG_TXT" ] || { warn "$CONFIG_TXT missing, skipping"; return; }
    local text kept
    text="$(cat "$CONFIG_TXT")"
    kept="$(printf '%s\n' "$text" | sed "/^${BEGIN}$/,/^${END}$/d")"
    kept="${kept%$'\n'}"
    if [ "$REVERT" -eq 1 ]; then
        _atomic_write "$CONFIG_TXT" "${kept}"$'\n'
        log "config.txt: removed camlab splash block"
        return
    fi
    log "Stage: config.txt"
    local block
    block="$(printf '%s\n%s\n%s' "$BEGIN" "disable_splash=1" "$END")"
    _atomic_write "$CONFIG_TXT" "${kept}"$'\n\n'"${block}"$'\n'
}

stage_theme() {
    if [ "$REVERT" -eq 1 ]; then
        plymouth-set-default-theme --reset >/dev/null 2>&1 || true
        rm -rf "$THEME_DST"
        log "removed Plymouth theme, reset default"
        return
    fi
    log "Stage: Plymouth theme"
    install -d -m 0755 "$THEME_DST"
    local f
    for f in camlab.plymouth camlab.script logo.png progress_bg.png progress_fill.png; do
        install -m 0644 "$SPLASH_SRC/$f" "$THEME_DST/"
    done
    plymouth-set-default-theme camlab
}

PREMOUNT_OVERRIDE="/etc/initramfs-tools/scripts/init-premount/plymouth"

stage_initramfs() {
    if [ "$REVERT" -eq 1 ]; then
        rm -f "$PREMOUNT_OVERRIDE"
        log "initramfs premount override removed"
        return
    fi
    log "Stage: initramfs (defer splash until KMS)"
    install -d -m 0755 "$(dirname "$PREMOUNT_OVERRIDE")"
    install -m 0755 "$SPLASH_SRC/initramfs-premount" "$PREMOUNT_OVERRIDE"
}

stage_console() {
    if [ "$REVERT" -eq 1 ]; then
        rm -f "$QUIET_DROPIN"
        systemctl unmask getty@tty1.service >/dev/null 2>&1 || true
        log "console output restored (drop-in removed, getty unmasked)"
        return
    fi
    log "Stage: console quiet"
    install -d -m 0755 "$(dirname "$QUIET_DROPIN")"
    _atomic_write "$QUIET_DROPIN" '[Manager]'$'\n''ShowStatus=no'$'\n'
    chmod 0644 "$QUIET_DROPIN"
    systemctl mask getty@tty1.service >/dev/null 2>&1 || true
}

SHUTDOWN_SPLASH="/usr/local/bin/camlab-shutdown-splash"
CAMLAB_DROPIN="/etc/systemd/system/camlab.service.d/shutdown-splash.conf"

stage_shutdown() {
    if [ "$REVERT" -eq 1 ]; then
        rm -f "$SHUTDOWN_SPLASH" "$CAMLAB_DROPIN"
        rmdir /etc/systemd/system/camlab.service.d 2>/dev/null || true
        systemctl daemon-reload >/dev/null 2>&1 || true
        log "removed shutdown splash hook"
        return
    fi
    log "Stage: shutdown splash"
    install -m 0755 "$REPO_DIR/scripts/camlab-shutdown-splash.sh" "$SHUTDOWN_SPLASH"
    install -d -m 0755 "$(dirname "$CAMLAB_DROPIN")"
    install -m 0644 "$REPO_DIR/deploy/systemd/camlab.service.d/shutdown-splash.conf" "$CAMLAB_DROPIN"
    systemctl daemon-reload
}

if [ "$REVERT" -eq 1 ]; then
    header "Boot splash - reverting"
else
    header "Boot splash - installing"
fi

stage_packages
stage_cmdline
stage_config
stage_theme
stage_initramfs
stage_console
stage_shutdown

log "Refreshing initramfs..."
update-initramfs -u >/dev/null

if [ "$REVERT" -eq 1 ]; then
    log "Revert complete. Reboot to restore the stock console."
else
    log "Done. Reboot to see the splash: sudo reboot"
fi
