#!/usr/bin/env python3
"""Derive a Tiny Tapeout `tt_block_<W>x4` floorplan DEF from the `<W>x2` one.

The CMOS5L support tools (htfab/tt-support-tools, branch `cmos`) ship 6x4 but
no 8x4 block, while the Jane Street contest asks for an 8x4 allocation.  The
6x2 -> 6x4 templates differ only in die height, row count, vertical track
counts and the Y coordinate of the top-edge pins; applying exactly those
rules to 8x2 yields 8x4.  Regenerating 6x4 from 6x2 with this script gives a
byte-identical file, which is the validation `--check` performs.

Usage:
    gen_tile_def.py <tt_block_Wx2.def> <out.def>
    gen_tile_def.py --check <tt-support-tools/tech/ihp-sg13cmos5l/def>
"""
import re
import sys

ROW_PITCH = 3780        # CoreSite height in DEF units
ROWS_X2 = 81            # rows in a Wx2 block
ROWS_X4 = 186           # rows in a Wx4 block
DIE_H_X2, DIE_H_X4 = "313740", "710640"
PIN_Y_X2, PIN_Y_X4 = " 313240 )", " 710140 )"
TRACKS_Y = {"TRACKS Y 420 DO 746": "TRACKS Y 420 DO 1691",
            "TRACKS Y 1640 DO 137": "TRACKS Y 1640 DO 311"}


def convert(text: str) -> str:
    out = []
    for ln in text.splitlines():
        if ln.startswith("DIEAREA"):
            ln = ln.replace(DIE_H_X2, DIE_H_X4)
        m = re.match(r"ROW ROW_(\d+) CoreSite 2880 (\d+) (N|FS) DO (\d+) BY 1 STEP 480 0 ;", ln)
        if m:
            out.append(ln)
            if int(m.group(1)) == ROWS_X2 - 1:
                for k in range(ROWS_X2, ROWS_X4):
                    y = ROW_PITCH + k * ROW_PITCH
                    out.append(f"ROW ROW_{k} CoreSite 2880 {y} {'N' if k % 2 == 0 else 'FS'} "
                               f"DO {m.group(4)} BY 1 STEP 480 0 ;")
            continue
        for old, new in TRACKS_Y.items():
            if ln.startswith(old):
                ln = ln.replace(old, new)
        if "PLACED" in ln:
            ln = ln.replace(PIN_Y_X2, PIN_Y_X4)
        out.append(ln)
    return "\n".join(out) + "\n"


def main(argv):
    if argv[1:2] == ["--check"]:
        d = argv[2]
        got = convert(open(f"{d}/tt_block_6x2_pgvdd.def").read())
        want = open(f"{d}/tt_block_6x4_pgvdd.def").read()
        print("6x4 regeneration identical" if got == want else "MISMATCH")
        return 0 if got == want else 1
    src, dst = argv[1], argv[2]
    open(dst, "w").write(convert(open(src).read()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
