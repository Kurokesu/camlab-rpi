#!/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Check pins in apt-packages against apt archive and loaded stack.
#
# Reports under three prefixes: archive, resolve and runtime. Runtime is skipped
# on a unit carrying no libcamera bindings, but any other failure is fatal.
#
# Reads apt index, so it wants a Pi carrying Kurokesu archive rather than CI.
# Changes nothing and needs no sudo.
#
# Usage: scripts/dev/check-stack-pin.sh

set -euo pipefail

# shellcheck disable=SC2034  # log tag read by common.sh
CAMLAB_TAG="stack-pin"

# shellcheck source=../common.sh
source "$(dirname "${BASH_SOURCE[0]}")/../common.sh"

for arg in "$@"; do
    case "$arg" in
        -h|--help) help_text; exit 0 ;;
        *) die "Unknown argument: $arg" ;;
    esac
done

# shellcheck source=../../apt-packages
source "$(resolve_repo_dir)/apt-packages"

# Fork pins apt enforces, then picamera2, which is recorded only
PINNED=("$LIBCAMERA_VERSION $LIBCAMERA_PACKAGES"
        "$RPICAM_APPS_VERSION $RPICAM_APPS_PACKAGES")
RECORDED=("$PICAMERA2_VERSION $PICAMERA2_PACKAGES")

FAILED=0
fail() { warn "$*"; FAILED=1; }

# Published version inside [floor, floor.), empty when the archive serves none
published() {
    local pkg="$1" floor="$2" version
    while read -r version; do
        dpkg --compare-versions "$version" ge "$floor" || continue
        dpkg --compare-versions "$version" lt "$floor." || continue
        printf '%s\n' "$version"
        return
    done < <(apt-cache madison "$pkg" | awk -F'|' '{ gsub(/ /, "", $2); print $2 }')
}

# What camlab/stack.py cannot ask: which package owns the object ldd resolved
# ldd reports /lib paths, dpkg records the merged-usr ones, so resolve first
check_loaded() {
    local module name path owner version
    module="$(python3 -c 'import libcamera; print(libcamera.__file__)' 2>/dev/null)" || module=""
    if [ -z "$module" ]; then
        warn "runtime: no libcamera bindings on this unit, nothing loaded to check"
        return
    fi

    while read -r name path; do
        path="$(readlink -f "$path")"
        owner="$(dpkg -S "$path" 2>/dev/null | awk -F: 'NR == 1 { print $1 }')" || owner=""
        if [ -z "$owner" ]; then
            fail "runtime: $name resolves to $path, owned by no package"
            continue
        fi
        version="$(dpkg-query -Wf '${Version}' "$owner")"
        if dpkg --compare-versions "$version" ge "$LIBCAMERA_VERSION" &&
           dpkg --compare-versions "$version" lt "$LIBCAMERA_VERSION."; then
            log "runtime: $name from $owner $version"
        else
            fail "runtime: $name from $owner $version, outside the pin"
        fi
    done < <(
        printf 'bindings %s\n' "$module"
        ldd "$(dirname "$module")"/_libcamera*.so |
            awk '$1 ~ /^libcamera/ { print $1, $3 }'
    )
}

header "Checking camera stack pin"

for pin in "${PINNED[@]}" "${RECORDED[@]}"; do
    read -r floor packages <<<"$pin"
    for pkg in $packages; do
        version="$(published "$pkg" "$floor")"
        if [ -n "$version" ]; then
            log "archive: $pkg $version"
        else
            fail "archive: nothing in [$floor, $floor.) published for $pkg"
        fi
    done
done

mapfile -t RELATIONS < <(stack_relations "${PINNED[@]}")

if resolution="$(apt-get satisfy -s --no-install-recommends "${RELATIONS[@]}" 2>&1)"; then
    log "resolve: apt satisfies the pinned set"
else
    fail "resolve: apt cannot satisfy the pinned set"
    printf '%s\n' "$resolution" | sed -n '/unmet dependencies/,$p' >&2
fi

check_loaded

[ "$FAILED" -eq 0 ] || die "Stack pin check failed"

log "Done. Stack pin holds."
