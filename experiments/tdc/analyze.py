#!/usr/bin/env python3
"""Check a tdc_probe run: did the delay chain survive, and what does STA say?"""
import glob, json, re, sys
run = sys.argv[1] if len(sys.argv) > 1 else "runs/probe"
nl = glob.glob(f"{run}/final/nl/*.nl.v")
if not nl:
    sys.exit("no final netlist; the run did not finish")
text = open(nl[0]).read()
cells = re.findall(r"^\s*(sg13cmos5l_\w+)\s+\S+\s*\(", text, re.M)
from collections import Counter
c = Counter(cells)
print("delay cells:", c.get("sg13cmos5l_dlygate4sd2_1", 0), "(expected 64)")
print("buffers inserted on chain nets:", sum(v for k, v in c.items() if "buf" in k))
print("all cells:", sum(c.values()))
m = json.load(open(f"{run}/final/metrics.json"))
for k in ("timing__setup__ws", "timing__hold__ws", "design__instance__area__stdcell", "route__drc_errors"):
    print(k, "=", m.get(k))
# chain nets still named tap[...] ?
print("chain nets kept:", len(set(re.findall(r"dlytap\[(\d+)\]", text))))
# per-stage delay: arrival time of the input edge at the last snap flop
for rpt in glob.glob(f"{run}/*-openroad-stapostpnr/nom_typ_1p20V_25C/max.rpt"):
    txt = open(rpt).read()
    for blk in txt.split("Startpoint:")[1:]:
        if blk.lstrip().startswith("edge_in"):
            m = re.search(r"data arrival time\s+([\d.]+)", blk)
            n = len(re.findall(r"dlygate4sd2_1", blk))
            print(f"typ corner: edge_in path through {n} delay cells, arrival {m.group(1) if m else '?'} ns"
                  f"{' -> ' + format(float(m.group(1))/n, '.3f') + ' ns/stage' if m and n else ''}")
            break
