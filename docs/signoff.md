# Tapeout sign-off record

Recorded on 2026-09-14 for the sign-off commit (design with host FIFO and CRC-32) of this repository, CMOS5L flow
(LibreLane 3.0.0rc1, IHP-Open-PDK `dev` + ihp-sg13cmos5l `ae76139`), tile
allocation 8x4 (1724.16 x 710.64 um), 40 MHz clock target (25 ns period).

## Summary

| Check | Tool | Result |
|-------|------|--------|
| Lint | Verilator 5.046 `-Wall`, both RTL and `-DSYNTH` configurations | clean |
| RTL simulation | cocotb 2.0.1 + Icarus 13, 12 tests, model lock-step every clock | 12/12 pass (`cd test && make`) |
| FPGA netlist simulation | same suite on the `synth_ice40` netlist (block-RAM memories) | 12/12 pass (`make FPGA=yes`) |
| Formal | SymbiYosys 0.69 + z3, 9 safety properties | k-induction proof (depth 6) and BMC depth 24 pass |
| Constrained random | 4 seeds x 1500 cycles per default run, two engines, random pads/masks/FIFO traffic | pass; status word, FIFO level and trace buffer match the model |
| Synthesis | Yosys (LibreLane) | 30 397 cells, 604 765 um2 before P&R buffering; 6 711 flops; 0 check errors |
| Place and route | OpenROAD (LibreLane) | routed, 0 routing DRC errors, 0 antenna violations after 36 diodes |
| Timing | OpenSTA, nom_typ / min / max corners | setup and hold met at every corner, TNS 0 (see below) |
| DRC | Magic (flow step 62) | 0 errors, 0 illegal overlaps |
| Precheck | tt-support-tools `precheck.py` with KLayout 0.30.12 (CMOS5L DRC, pin-label overlap, zero area, pin, boundary, layer, cell-name, analog-pin checks) | all 9 checks pass on the final GDS with the generated 8x4 template |
| LVS | netgen (flow step 66) | 0 device/net/pin mismatches |
| Gate-level simulation | cocotb on the final netlist with CMOS5L cell models | 12/12 pass on `final/nl` netlist (`make GATES=yes`) |
| FPGA bitstream | Yosys + nextpnr-ice40 (TT ASIC-sim UP5K board) | builds: 2 338 / 5 280 logic cells (44 %), 5 block RAMs, Fmax 15.0 MHz (board clock 12 MHz) |

## Area and utilisation

| Metric | Value |
|--------|-------|
| Die (8x4) | 1724.16 x 710.64 um = 1.225 mm2 |
| Standard-cell instances (no fill) | 42 181 (23 041 combinational, 6 711 sequential, 10 776 timing-repair buffers, clock tree, 36 antenna diodes) |
| Standard-cell area | 762 990 um2 |
| Utilisation | 63.2 % |
| Routed wirelength | 1.76 m |
| Estimated total power (typ) | 19.0 mW |

The 10 776 timing-repair buffers are hold fixes (6 802 hold buffers) against
the 0.3 ns clock skew of a 6 700-sink clock tree; the sequential and
combinational logic alone is about 600 kum2 (50 %).  The 128-byte FIFO and
CRC-32 added roughly 1 100 flops and 10 % utilisation.

## Timing

Clock period 25 ns (40 MHz), generic Tiny Tapeout SDC (0.25 ns uncertainty, 5 % derate).

| Corner | Setup worst slack | Hold worst slack | TNS |
|--------|------------------|------------------|-----|
| nom_slow_1p08V_125C | +7.71 ns | +0.650 ns | 0 |
| nom_typ_1p20V_25C | +13.96 ns | +0.314 ns | 0 |
| nom_fast_1p32V_m40C | +17.84 ns | +0.120 ns | 0 |

The critical path is flop to flop through the 128:1 instruction read
multiplexer and the engine decode; at the slow corner it leaves 7.7 ns of
margin, so the 40 MHz Ethernet timing is retained.  The reports flag 12
max-slew pins at the slow corner and 458 max-fanout pins, almost all clock-tree leaf buffers driving 17 sinks against
the generic SDC's limit of 8; the flow treats both as informational and the
design closes with `design__violations = 0`.  LVS (netgen): 0 mismatches,
0 unmatched devices, nets or pins.

## Notes and limitations

* The current CMOS5L support tools do not ship an 8x4 tile block.
  `flow/gen_tile_def.py` derives it from the 8x2 block with exactly the
  6x2 -> 6x4 transformation (verified byte-identical on 6x4); the GDS
  workflow applies it before hardening and before the precheck.  The
  upstream precheck and gate-level actions are additionally broken by
  their PDK installer at the moment, so the workflow spells those jobs out
  with `flow/install_pdk.sh`; the viewer job needs GitHub Pages enabled.
* No SRAM macro exists for the CMOS5L slim PDK, so the program memories are
  flop arrays (128 x 16 per engine).  A 256-word memory would need either a
  latch-based array or a macro; both are left as future work.
* The Manchester transmitter streams a complete frame (preamble, SFD, up to
  128 payload bytes from the host FIFO, hardware CRC-32 FCS) at 10 Mbit/s;
  the receiver decodes a stream into the trace buffer and the host checks
  the FCS.  Frames longer than the FIFO need the host to refill it while
  the engine drains it, which the SPI link cannot sustain at line rate; the
  chip does not drive an MDI directly.
* The host link samples CFG_SCK with the 40 MHz core clock and needs
  SCK <= 5 MHz.
