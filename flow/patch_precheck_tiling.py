#!/usr/bin/env python3
"""Make tt-support-tools' precheck run the CMOS5L KLayout DRC in tiled mode.

The IHP deck defaults to `deep` (hierarchical) mode, which takes hours on this
1.2 mm2 block; `run_mode=tiling` (500 um tiles, one thread per core) reports the
same violations (none) in well under a minute.  Usage: patch_precheck_tiling.py
<path/to/precheck.py>
"""
import sys

path = sys.argv[1]
src = open(path).read()
old = '''        f"{PDK_ROOT}/{PDK_NAME}/libs.tech/klayout/tech/drc/ihp-sg13cmos5l.drc",
    )'''
new = '''        f"{PDK_ROOT}/{PDK_NAME}/libs.tech/klayout/tech/drc/ihp-sg13cmos5l.drc",
        extra_vars={"run_mode": "tiling"},
    )'''
if new in src:
    print("already patched")
elif old in src:
    open(path, "w").write(src.replace(old, new))
    print("patched", path)
else:
    sys.exit("precheck.py layout not recognised; upstream changed?")
