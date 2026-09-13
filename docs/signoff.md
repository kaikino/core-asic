# Tapeout sign-off record

Recorded on 2026-09-13/14 for the sign-off commit of this repository, CMOS5L flow
(LibreLane 3.0.0rc1, IHP-Open-PDK `dev` + ihp-sg13cmos5l `ae76139`), tile
allocation 8x4 (1724.16 x 710.64 um), 40 MHz clock target (25 ns period).

## Summary

| Check | Tool | Result |
|-------|------|--------|
| Lint | Verilator 5.046 `-Wall`, both RTL and `-DSYNTH` configurations | clean |
| RTL simulation | cocotb 2.0.1 + Icarus 13, 11 tests, model lock-step every clock | 11/11 pass (`make -C test`) |
| FPGA netlist simulation | same suite on the `synth_ice40` netlist (block-RAM memories) | 11/11 pass (`make -C test FPGA=yes`) |
| Formal | SymbiYosys 0.69 + z3, 8 safety properties | k-induction proof (depth 6) and BMC depth 24 pass |
| Constrained random | 4 seeds x 1500 cycles per default run, two engines, random pads/masks | pass, status word and trace buffer match the model |
| Synthesis | Yosys (LibreLane) | 25 790 cells, 505 453 um2 before P&R buffering; 5 629 flops; 0 check errors |
| Place and route | OpenROAD (LibreLane) | routed, 0 routing DRC errors (5 iterations), 0 antenna violations after 114 diodes |
| Timing | OpenSTA, nom_typ / min / max corners | setup and hold met at every corner, TNS 0 (see below) |
| DRC | Magic (flow step 62) | 0 errors, 0 illegal overlaps |
| Precheck | tt-support-tools `precheck.py` (KLayout DRC, pin, boundary, layer checks) | cell-name and analog-pin checks pass; the KLayout checks did not complete locally (the Docker KLayout ran the CMOS5L deck for 6 h without finishing and the native build was blocked by Gatekeeper); left to the CI precheck job |
| LVS | netgen (flow step 66) | 0 device/net/pin mismatches |
| Gate-level simulation | cocotb on the final netlist with CMOS5L cell models | 11/11 pass on `final/nl` netlist (`make -C test GATES=yes`, 297 s) |
| FPGA bitstream | Yosys + nextpnr-ice40 (TT ASIC-sim UP5K board) | builds: 2 008 / 5 280 logic cells (38 %), 4 block RAMs, Fmax 15.5 MHz (board clock 12 MHz) |

## Area and utilisation

| Metric | Value |
|--------|-------|
| Die (8x4) | 1724.16 x 710.64 um = 1.225 mm2 |
| Standard-cell instances (no fill) | 35 857 (19 537 combinational, 5 629 sequential, 9 198 timing-repair buffers, 755 clock tree, 114 antenna diodes) |
| Standard-cell area | 638 968 um2 |
| Utilisation | 52.9 % |
| Routed wirelength | 1.48 m |
| Estimated total power (typ) | 15.7 mW |

The 9 198 timing-repair buffers are hold fixes (5 731 hold buffers) against
the 0.3 ns clock skew of a 5 630-sink clock tree; the sequential and
combinational logic alone is 502 kum2 (41 %).

## Timing

Clock period 25 ns (40 MHz), generic Tiny Tapeout SDC (0.25 ns uncertainty, 5 % derate).

| Corner | Setup worst slack | Hold worst slack | TNS |
|--------|------------------|------------------|-----|
| nom_slow_1p08V_125C | +8.21 ns | +0.638 ns | 0 |
| nom_typ_1p20V_25C | +14.48 ns | +0.308 ns | 0 |
| nom_fast_1p32V_m40C | +18.18 ns | +0.115 ns | 0 |

The critical path is flop to flop through the 128:1 instruction read
multiplexer and the engine decode; at the slow corner it leaves 8.2 ns of
margin, so the 40 MHz Ethernet timing is retained.  The reports flag 36
max-slew pins at the slow corner (3.2 ns against a 2.5 ns limit) and 394
max-fanout pins, almost all clock-tree leaf buffers driving 17 sinks against
the generic SDC's limit of 8; the flow treats both as informational and the
design closes with `design__violations = 0`.  LVS (netgen): 0 mismatches,
0 unmatched devices, nets or pins.

## Notes and limitations

* The current CMOS5L support tools do not ship an 8x4 tile block.
  `flow/gen_tile_def.py` derives it from the 8x2 block with exactly the
  6x2 -> 6x4 transformation (verified byte-identical on 6x4); the GDS
  workflow applies it before hardening.  The official precheck/GL-test
  actions will only accept 8x4 once upstream adds the block.
* No SRAM macro exists for the CMOS5L slim PDK, so the program memories are
  flop arrays (128 x 16 per engine).  A 256-word memory would need either a
  latch-based array or a macro; both are left as future work.
* The Manchester programs demonstrate 10 Mbit/s symbol timing with a
  preamble, SFD and short immediate payload.  Streaming full frames would
  need a data FIFO faster than the host link; the chip does not drive an
  MDI directly.
* The host link samples CFG_SCK with the 40 MHz core clock and needs
  SCK <= 5 MHz.
