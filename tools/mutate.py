#!/usr/bin/env python3
"""Mutation check: inject small bugs into the RTL and confirm the cocotb suite fails.

Each mutant is a (file, original, replacement, description).  The script
applies one mutant at a time, runs the chosen test modules in a separate
build directory, restores the file, and reports whether the suite caught it.

    python tools/mutate.py            # all mutants, core+protocols modules
    python tools/mutate.py -m test_core -k 0 2
"""
import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MUTANTS = [
    ("src/proto_pio_engine.sv", "2'd1: begin regs[rd] <= dec_val; program_counter <= (dec_val != 8'd0) ? imm : pc_next; end",
     "2'd1: begin regs[rd] <= dec_val; program_counter <= (dec_val == 8'd0) ? imm : pc_next; end",
     "DJNZ branches on zero instead of non-zero"),
    ("src/proto_pio_engine.sv", "2'd1: uio_oe   <= (uio_oe   & ~imm) | (rd_val & imm);",
     "2'd1: uio_oe   <= (uio_oe   & ~imm) | (rd_val | imm);", "OUT to UIO_OE ignores the mask"),
    ("src/tt_um_kaikino_protocol_emu.sv", "wire [7:0] drv_uio1 = req_uio1 & ~coll_uio;",
     "wire [7:0] drv_uio1 = req_uio1;", "collision no longer disables engine 1's drive"),
    ("src/tt_um_kaikino_protocol_emu.sv", "crc_step = (x >> 1) ^ (x[0] ? poly : 32'd0);",
     "crc_step = (x >> 1) ^ (x[0] ? 32'd0 : poly);", "CRC polynomial applied on the wrong bit"),
    ("src/proto_pio_engine.sv", "(rd == 2'd2) ? (pin_now & ~pin_was) : (~pin_now & pin_was);",
     "(rd == 2'd2) ? (pin_now & pin_was) : (~pin_now & pin_was);", "WAIT RISE fires on a steady high"),
    ("src/tt_um_kaikino_protocol_emu.sv", "if (armed & trig_hit) begin armed <= 1'b0; triggered <= 1'b1; end",
     "if (armed & trig_hit) begin armed <= 1'b0; triggered <= 1'b0; end", "trigger never enters the triggered state"),
    ("src/proto_tdc.sv", "wire [STAGES-1:0] matched = lvl ? snap : ~snap;",
     "wire [STAGES-1:0] matched = snap;", "TDC counts ones regardless of level"),
]


def run_suite(modules: str) -> bool:
    env = dict(os.environ, COCOTB_TEST_MODULES=modules, COCOTB_RESULTS_FILE="results_mut.xml",
               SIM_BUILD="sim_build/mut")
    r = subprocess.run(["make", "-B", f"COCOTB_TEST_MODULES={modules}", "SIM_BUILD=sim_build/mut",
                        "COCOTB_RESULTS_FILE=results_mut.xml"], cwd=os.path.join(ROOT, "test"),
                       env=env, capture_output=True, text=True)
    out = r.stdout + r.stderr
    return "FAIL=0" in out and "TESTS=" in out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-m", "--modules", default="test_core,test_protocols")
    ap.add_argument("-k", "--keep", nargs="*", type=int, help="mutant indices to run (default all)")
    args = ap.parse_args()
    indices = args.keep if args.keep else range(len(MUTANTS))
    caught = 0
    for i in indices:
        path, old, new, desc = MUTANTS[i]
        full = os.path.join(ROOT, path)
        src = open(full).read()
        assert old in src, f"mutant {i}: pattern not found in {path}"
        shutil.copy(full, full + ".orig")
        try:
            open(full, "w").write(src.replace(old, new))
            passed = run_suite(args.modules)
        finally:
            shutil.move(full + ".orig", full)
        verdict = "SURVIVED (suite passed)" if passed else "caught"
        caught += not passed
        print(f"mutant {i}: {desc}: {verdict}")
    print(f"{caught}/{len(list(indices))} mutants caught")
    return 0 if caught == len(list(indices)) else 1


if __name__ == "__main__":
    sys.exit(main())
