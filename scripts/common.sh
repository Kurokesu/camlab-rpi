#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Shared helpers sourced by install.sh, scripts/camlabctl.sh and scripts/setup/*.
# Provides colored logging (log/warn/die/header), repo-root resolution and
# camlab-owner detection that works under sudo.

# Terminal colors. Detect TTY on first source and pin via CAMLAB_COLOR, so
# children keep colors after a parent redirects stdout through a tee pipe.
if [ -z "${CAMLAB_COLOR:-}" ] && [ -t 1 ]; then
    export CAMLAB_COLOR=1
fi

if [ -n "${CAMLAB_COLOR:-}" ]; then
    _C_RED=$'\033[0;31m'
    _C_GREEN=$'\033[0;32m'
    _C_YELLOW=$'\033[1;33m'
    _C_CYAN=$'\033[0;36m'
    _C_RESET=$'\033[0m'
else
    _C_RED=''; _C_GREEN=''; _C_YELLOW=''; _C_CYAN=''; _C_RESET=''
fi

# Primitives set CAMLAB_TAG before sourcing, else fall back to "camlab".
: "${CAMLAB_TAG:=camlab}"

# camlab owner: whoever ran sudo, else current user.
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    CAMLAB_USER="$SUDO_USER"
else
    CAMLAB_USER="$(whoami)"
fi

# shellcheck disable=SC2034  # read by sourcing scripts
CAMLAB_UID="$(id -u "$CAMLAB_USER")"
# shellcheck disable=SC2034
CAMLAB_HOME="$(getent passwd "$CAMLAB_USER" | cut -d: -f6)"

log()    { echo -e "${_C_GREEN}[${CAMLAB_TAG}]${_C_RESET} $*"; }
warn()   { echo -e "${_C_YELLOW}[${CAMLAB_TAG}]${_C_RESET} $*" >&2; }
die()    { echo -e "${_C_RED}[${CAMLAB_TAG}]${_C_RESET} $*" >&2; exit 1; }
header() { echo; echo -e "${_C_CYAN}=== $* ===${_C_RESET}"; echo; }

# Repo root from a caller at scripts/setup/*. BASH_SOURCE[1] is the caller path.
resolve_repo_dir() {
    (cd "$(dirname "${BASH_SOURCE[1]}")/../.." && pwd)
}

# Primitives touching /etc, /boot or systemd call this first.
require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        die "This script must be run as root (use sudo)."
    fi
}

# eatmydata skips dpkg per-package fsyncs, slow on eMMC/SD and pointless for a
# re-runnable install.
apt_get() {
    if command -v eatmydata >/dev/null 2>&1; then
        eatmydata apt-get "$@"
    else
        apt-get "$@"
    fi
}

# Write via a temp file in the same dir, so readers never see a half-written
# boot-critical file. Mode of an existing file is preserved.
atomic_write() {
    local path="$1" content="$2" tmp
    tmp="$(mktemp "${path}.camlab-XXXXXX")"
    printf '%s' "$content" > "$tmp"
    if [ -f "$path" ]; then chmod --reference="$path" "$tmp" 2>/dev/null || true; fi
    mv -f "$tmp" "$path"
}

# Managed-block editing. Each setup script owns a marker pair, so edits to shared
# files (config.txt, fstab) stay greppable and removable.

# Drop the block between begin/end markers (no-op if file or block absent).
block_strip() {
    local path="$1" begin="$2" end="$3" kept
    [ -f "$path" ] || return 0
    kept="$(sed "/^${begin}$/,/^${end}$/d" "$path")"
    kept="${kept%$'\n'}"
    atomic_write "$path" "${kept}"$'\n'
}

# Strip any existing copy, then append content wrapped in the markers.
block_write() {
    local path="$1" begin="$2" end="$3" content="$4" kept block
    block_strip "$path" "$begin" "$end"
    kept="$(cat "$path")"
    block="$(printf '%s\n%s\n%s' "$begin" "$content" "$end")"
    atomic_write "$path" "${kept%$'\n'}"$'\n\n'"${block}"$'\n'
}

# cmdline.txt token editing. Whole-token match, one token at a time, so tokens
# owned by other scripts survive.
cmdline_has() { tr ' ' '\n' < "$1" | grep -qFx "$2"; }

cmdline_add() {
    local path="$1" token="$2"
    cmdline_has "$path" "$token" && return 0
    sed -i "s/[[:space:]]*\$/ ${token}/" "$path"
}

cmdline_remove() {
    local path="$1" token="$2" line
    line="$(awk -v t="$token" '{
        out = ""
        for (i = 1; i <= NF; i++)
            if ($i != t) out = out (out == "" ? "" : " ") $i
        print out
    }' "$path")"
    atomic_write "$path" "$line"$'\n'
}

# Print the caller's top-of-file description block as help text. A lone "#" line
# separates it from the SPDX header. It ends at the first non-comment line.
help_text() {
    awk '
        !in_desc && /^#$/ { in_desc=1; next }
        in_desc && /^#/   { sub(/^# ?/, ""); print; next }
        in_desc           { exit }
    ' "${BASH_SOURCE[1]}"
}
