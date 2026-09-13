#!/usr/bin/env bash
# Local hardening with the Tiny Tapeout CMOS5L flow (LibreLane in Docker).
#
# Mirrors TinyTapeout/tt-gds-action@ihp-cmos5l (pdk: ihp-sg13cmos5l) and adds
# the 8x4 floorplan template that the current support tools lack:
#   flow/gen_tile_def.py derives tech/ihp-sg13cmos5l/def/tt_block_8x4_pgvdd.def
#   from the official 8x2 block and 8x4 is appended to tile_sizes.yaml.
#
# Prerequisites (see docs/signoff.md): Docker, a Python venv with the
# support-tools requirements and librelane==3.0.0rc1, and the PDK installed
# with flow/install_pdk.sh into $PDK_ROOT.
set -euo pipefail
cd "$(dirname "$0")/.."
: "${PDK_ROOT:?set PDK_ROOT to the directory prepared by flow/install_pdk.sh}"
TT_DIR="${TT_DIR:-.work/tt}"
TILES="${TILES:-}"   # optional override of info.yaml tiles, e.g. TILES=6x4

if [ ! -d "$TT_DIR" ]; then
  git clone -q --depth 1 -b cmos https://github.com/htfab/tt-support-tools "$TT_DIR"
fi
DEF_DIR="$TT_DIR/tech/ihp-sg13cmos5l/def"
if [ ! -f "$DEF_DIR/tt_block_8x4_pgvdd.def" ]; then
  python3 flow/gen_tile_def.py --check "$DEF_DIR"
  python3 flow/gen_tile_def.py "$DEF_DIR/tt_block_8x2_pgvdd.def" "$DEF_DIR/tt_block_8x4_pgvdd.def"
  grep -q '^8x4:' "$TT_DIR/tech/ihp-sg13cmos5l/tile_sizes.yaml" || \
    echo '8x4: "0 0 1724.16 710.64"' >> "$TT_DIR/tech/ihp-sg13cmos5l/tile_sizes.yaml"
fi
rm -f tt && ln -s "$TT_DIR" tt
# The support tools record the git remote; tolerate a checkout without one.
python3 - "$TT_DIR/project.py" <<'PY'
import sys
p = sys.argv[1]; s = open(p).read()
old = "        return list(Repo(self.local_dir).remotes[0].urls)[0]"
new = ("        try:\n            return list(Repo(self.local_dir).remotes[0].urls)[0]\n"
       "        except IndexError:  # local checkout without a remote\n            return \"\"")
if old in s and "except IndexError" not in s:
    open(p, "w").write(s.replace(old, new))
PY

if [ -n "$TILES" ]; then
  cp info.yaml .work/info.yaml.orig
  sed -i '' "s/^  tiles: .*/  tiles: \"$TILES\"/" info.yaml
  trap 'mv .work/info.yaml.orig info.yaml' EXIT
fi

python tt/tt_tool.py --create-user-config --ihp
python tt/tt_tool.py --harden --ihp
python tt/tt_tool.py --print-warnings --ihp || true
python tt/tt_tool.py --print-stats --ihp || true
python tt/tt_tool.py --print-cell-category --ihp || true
