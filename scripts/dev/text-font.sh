#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Rebuild bundled Roboto faces from Google variable font.
# Latin only, two weights: regular for text, medium for 500 rules in style.py.
# Debian ships a 2017 unhinted snapshot, hence bundling.
#
# Needs python3-fonttools and network access.
#
# Usage: scripts/dev/text-font.sh

set -euo pipefail

# Freeze TTF head.modified timestamp, else every rebuild differs.
export SOURCE_DATE_EPOCH=0

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ASSETS="$REPO/camlab/assets"
# Pinned commit keeps rebuilds byte-reproducible.
FONT_REF="1c627bfa375fc51cf86fabeca4f6e08a95f0aa5c"
FONT_URL="https://github.com/google/fonts/raw/$FONT_REF/ofl/roboto/Roboto%5Bwdth%2Cwght%5D.ttf"

# Google's latin subset plus arrows the updates card draws.
UNICODES="U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329"
UNICODES="$UNICODES,U+2000-206F,U+2074,U+20AC,U+2122,U+2190-2193,U+2212,U+2215,U+FEFF,U+FFFD"

python3 -c "import fontTools" 2>/dev/null || { echo "fontTools missing: sudo apt install python3-fonttools" >&2; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

curl -fsSL -o "$TMP/full.ttf" "$FONT_URL"
# Instance axes first: static weight subsets smaller than variable font.
for face in Regular:400 Medium:500; do
    name="${face%%:*}"
    weight="${face##*:}"
    # Names each instance from its STAT entry, else both call themselves Regular
    # and Qt picks one. --name-IDs keeps the typographic names that carry it.
    python3 -m fontTools.varLib.instancer --update-name-table \
        -o "$TMP/$name.ttf" "$TMP/full.ttf" wdth=100 "wght=$weight"
    python3 -m fontTools.subset "$TMP/$name.ttf" \
        --unicodes="$UNICODES" \
        --name-IDs='*' \
        --output-file="$ASSETS/Roboto-$name.ttf"
    echo "Wrote $ASSETS/Roboto-$name.ttf ($(stat -c%s "$ASSETS/Roboto-$name.ttf") bytes)"
done
