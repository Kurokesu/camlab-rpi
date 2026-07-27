#!/usr/bin/bash
# SPDX-FileCopyrightText: 2026 UAB Kurokesu
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Rebuild camlab/assets/MaterialSymbolsOutlined.ttf from Google's variable
# font, keeping only the glyphs listed in camlab/gui/icons.py. Run after
# adding a codepoint there.
#
# Needs python3-fonttools and network access.
#
# Usage: scripts/dev/icon-font.sh

set -euo pipefail

# Freeze TTF head.modified timestamp, else every rebuild differs.
export SOURCE_DATE_EPOCH=0

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ASSET="$REPO/camlab/assets/MaterialSymbolsOutlined.ttf"
# Pinned commit keeps rebuilds byte-reproducible, bump when adding glyphs.
FONT_REF="528cb964c01fb2b09bc3b9208f82b6d8f8c1c1e2"
FONT_URL="https://github.com/google/material-design-icons/raw/$FONT_REF/variablefont/MaterialSymbolsOutlined%5BFILL%2CGRAD%2Copsz%2Cwght%5D.ttf"

python3 -c "import fontTools" 2>/dev/null || { echo "fontTools missing: sudo apt install python3-fonttools" >&2; exit 1; }

# Codepoints are the single source of truth, so the asset cannot drift from
# the names the app asks for.
mapfile -t CPS < <(python3 - "$REPO" <<'PY'
import re, sys, pathlib
src = (pathlib.Path(sys.argv[1]) / "camlab/gui/icons.py").read_text()
block = re.search(r"_CODEPOINTS[^{]*\{(.*?)\}", src, re.S).group(1)
for cp in re.findall(r"0[xX]([0-9A-Fa-f]+)", block):
    print(f"U+{cp.upper()}")
PY
)
[ "${#CPS[@]}" -gt 0 ] || { echo "no codepoints parsed from icons.py" >&2; exit 1; }
echo "Keeping ${#CPS[@]} glyphs"

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

curl -fsSL -o "$TMP/full.ttf" "$FONT_URL"
# Pin the variable axes first: the app renders one static weight, and an
# instanced font subsets far smaller than the variable original.
python3 -m fontTools.varLib.instancer -o "$TMP/static.ttf" "$TMP/full.ttf" \
    FILL=0 GRAD=0 opsz=24 wght=400
python3 -m fontTools.subset "$TMP/static.ttf" \
    --unicodes="$(IFS=,; echo "${CPS[*]}")" \
    --output-file="$ASSET"

echo "Wrote $ASSET ($(stat -c%s "$ASSET") bytes)"
