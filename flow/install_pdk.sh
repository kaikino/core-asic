#!/usr/bin/env bash
# Install the ihp-sg13cmos5l PDK into $PDK_ROOT exactly as
# TinyTapeout/tt-gds-action@ihp-cmos5l does (pinned revisions, flow patches),
# using shallow clones.  Usage: PDK_ROOT=/path/to/pdk flow/install_pdk.sh
set -euo pipefail
: "${PDK_ROOT:?}"
rm -rf "$PDK_ROOT"
git clone -q --depth 1 --branch dev https://github.com/IHP-GmbH/IHP-Open-PDK.git "$PDK_ROOT"
rm -rf "$PDK_ROOT/ihp-sg13cmos5l"; mkdir -p "$PDK_ROOT/ihp-sg13cmos5l" && cd "$PDK_ROOT/ihp-sg13cmos5l"
git init -q && git remote add origin https://github.com/IHP-GmbH/ihp-sg13cmos5l.git
git fetch -q --depth 1 origin ae7613984daf3ac2b14897321399df497278068f
git checkout -q FETCH_HEAD
ln -sf openrcx/IHP_rcx_patterns.rules "$PDK_ROOT/ihp-sg13g2/libs.tech/librelane/IHP_rcx_patterns.rules"
python3 - <<'PYEOF'
import pathlib, subprocess
cfg = pathlib.Path("libs.tech/librelane/config.tcl")
text = cfg.read_text()
patch = "\n".join([
    '## magic setup',
    'set ::env(MAGICRC) "$::env(PDK_ROOT)/$::env(PDK)/libs.tech/magic/ihp-sg13cmos5l.magicrc"',
    'set ::env(MAGIC_TECH) "$::env(PDK_ROOT)/$::env(PDK)/libs.tech/magic/ihp-sg13cmos5l.tech"',
    '', '# netgen setup',
    'set ::env(NETGEN_SETUP) "$::env(PDK_ROOT)/$::env(PDK)/libs.tech/netgen/ihp-sg13cmos5l_setup.tcl"', '',
]) + "\n"
cfg.write_text(text.replace("# GPIO Pads", patch + "# GPIO Pads"))
rev = subprocess.check_output(["git", "-C", "..", "rev-parse", "HEAD"], text=True).strip()
pathlib.Path("SOURCES").write_text(f"IHP-Open-PDK {rev}\n")
drc = pathlib.Path("libs.tech/klayout/tech/drc/ihp-sg13cmos5l.drc")
drc.write_text("\n".join(l for l in drc.read_text().splitlines()
                         if not ("%include rule_decks/" in l and "layers_def" not in l)) + "\n")
PYEOF
echo PDK_INSTALL_OK
