#!/usr/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026, UAB Kurokesu
#
# Shared helpers sourced by install.sh, scripts/camlabctl.sh, and the
# scripts/setup/* primitives.
#
# Provides: colored logging (log/warn/die/header), repo-root resolution
# (for callers under scripts/setup/), and camlab-owner detection that
# works under sudo.
#
# Lifted from cinepi-kurokesu/scripts/common.sh, tag swapped to camlab.

# Terminal colors. Detect TTY on first source and pin the result via
# CAMLAB_COLOR so child scripts keep colors even after a parent (e.g.
# install.sh) redirects stdout through a tee pipe.
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

# Each primitive sets CAMLAB_TAG before sourcing this file (or falls back to "camlab").
: "${CAMLAB_TAG:=camlab}"

# camlab owner - whoever ran sudo (if applicable), else the current user.
if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    CAMLAB_USER="$SUDO_USER"
else
    CAMLAB_USER="$(whoami)"
fi

CAMLAB_UID="$(id -u "$CAMLAB_USER")"
CAMLAB_HOME="$(getent passwd "$CAMLAB_USER" | cut -d: -f6)"

log()    { echo -e "${_C_GREEN}[${CAMLAB_TAG}]${_C_RESET} $*"; }
warn()   { echo -e "${_C_YELLOW}[${CAMLAB_TAG}]${_C_RESET} $*" >&2; }
die()    { echo -e "${_C_RED}[${CAMLAB_TAG}]${_C_RESET} $*" >&2; exit 1; }
header() { echo; echo -e "${_C_CYAN}=== $* ===${_C_RESET}"; echo; }

# Resolve the repository root from a caller at scripts/setup/*.
# Uses BASH_SOURCE[1] = caller's file path. '../..' walks up two dirs to repo root.
resolve_repo_dir() {
    (cd "$(dirname "${BASH_SOURCE[1]}")/../.." && pwd)
}

# Must-be-root guard. Primitives that touch /etc, /boot, systemd etc.
# should call this first.
require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        die "This script must be run as root (use sudo)."
    fi
}

# Print the caller's top-of-file description comment block as help text.
# Convention: a lone "#" line separates the SPDX/copyright header from the
# description block. The description ends at the first non-comment line.
help_text() {
    awk '
        !in_desc && /^#$/ { in_desc=1; next }
        in_desc && /^#/   { sub(/^# ?/, ""); print; next }
        in_desc           { exit }
    ' "${BASH_SOURCE[1]}"
}
