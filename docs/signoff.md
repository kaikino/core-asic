# Tapeout sign-off record

Recorded on 2026-09-21 for the sign-off commit (host FIFO, programmable CRC, sub-clock timing delay lines, full protocol library) of this repository, CMOS5L flow
(LibreLane 3.0.0rc1, IHP-Open-PDK `dev` + ihp-sg13cmos5l `ae76139`), tile
allocation 8x4 (1724.16 x 710.64 um), 40 MHz clock target (25 ns period).

## Summary

| Check | Tool | Result |
|-------|------|--------|
| Lint | Verilator 5.046 `-Wall`, both RTL and `-DSYNTH` configurations | clean |
| RTL simulation | cocotb 2.0.1 + Icarus 13, 31 tests, model lock-step every clock incl. delay-line fine times | 31/31 pass (`cd test && make`) |
| FPGA netlist simulation | same suite on the `synth_ice40` netlist (block-RAM memories, zero-delay chains) | 31/31 pass (`make FPGA=yes`) |
| Formal | SymbiYosys 0.69 + z3, 11 safety properties | k-induction proof (depth 6) and BMC depth 24 pass |
| Mutation check | `tools/mutate.py`, 7 injected RTL bugs vs core, protocol and timing tests | 7/7 caught |
| Constrained random | soak: 10 seeds x 3000 cycles plus 10 x 800 with timing channels; two engines, random pads, masks, FIFO and timing traffic | pass; status word, FIFO level and trace buffer match the model |
| Synthesis | Yosys (LibreLane) | 34 783 cells, 668 010 um2 before P&R buffering; 7 182 flops; 512 delay cells kept; 0 check errors |
| Place and route | OpenROAD (LibreLane) | routed (detailed routing 15 217 -> 5 272 -> 4 299 -> 157 -> 0 violations), 0 antenna violations after 36 diodes; all 512 delay cells intact |
| Timing | OpenSTA, nom_typ / min / max corners | setup and hold met at every corner, TNS 0 (see below) |
| DRC | Magic (flow step 62) | 0 errors, 0 illegal overlaps |
| Precheck | tt-support-tools `precheck.py` with KLayout 0.30.12, tiled DRC (`flow/patch_precheck_tiling.py`) | all 9 checks pass on the final GDS with the generated 8x4 template |
| LVS | netgen (flow step 66) | 0 device/net/pin mismatches |
| Gate-level simulation | cocotb on the final netlist with CMOS5L cell models (zero-delay chains) | 31/31 pass on `final/nl` netlist (`make GATES=yes`) |
| FPGA bitstream | Yosys + nextpnr-ice40 (TT ASIC-sim UP5K board) | builds: ~2 800 / 5 280 logic cells (53 %), 4 block RAMs, Fmax 14.8 MHz (board clock 12 MHz); delay lines stubbed |

## Area and utilisation

| Metric | Value |
|--------|-------|
| Die (8x4) | 1724.16 x 710.64 um = 1.225 mm2 |
| Standard-cell instances (no fill) | 47 191 (26 483 combinational, 7 182 sequential, 11 420 timing-repair buffers, 512 delay cells, clock tree, 36 antenna diodes) |
| Standard-cell area | 832 380 um2 |
| Utilisation | 68.9 % |
| Routed wirelength | 1.91 m |
| Estimated total power (typ) | 20.3 mW |

The 11 420 timing-repair buffers are hold fixes (6 991 hold buffers) against
the 0.3 ns clock skew of a 7 200-sink clock tree; the sequential and
combinational logic alone is about 670 kum2 (55 %).  The 128-byte FIFO and
programmable CRC cost about 11 % utilisation; the four 128-stage delay
lines with their sample flops, counters and tap multiplexers about 4 %.
A first version with 176-stage lines and two flop stages per tap reached
69-70 % and the detailed router did not converge within hours; the final
design at 68.9 % converges in four iterations.

## Timing

Clock period 25 ns (40 MHz), generic Tiny Tapeout SDC (0.25 ns uncertainty, 5 % derate).

| Corner | Setup worst slack | Hold worst slack | TNS |
|--------|------------------|------------------|-----|
| nom_slow_1p08V_125C | +5.91 ns | +0.641 ns | 0 |
| nom_typ_1p20V_25C | +6.46 ns | +0.310 ns | 0 |
| nom_fast_1p32V_m40C | +6.80 ns | +0.121 ns | 0 |

The worst path is the `rst_n` input (5 ns generic input delay plus the
reset distribution) with 5.9 ns of margin; the engine paths through the
128:1 instruction multiplexer keep more.  The delay lines are false paths
(`src/proto.sdc`) and are neither timed nor repaired; their stage delay
is 0.15 / 0.22 / 0.35 ns at the fast / typical / slow corner
(experiments/tdc).  The reports flag 15 max-slew pins at the slow corner
and 479 max-fanout pins, almost all clock-tree leaf buffers driving 17 sinks against
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
* Sub-clock timing: the 128-stage lines span a full period at the typical
  and slow corners and about 80 % of one at the fast corner; the
  calibration reference is half a period (12.5 ns) so it never saturates.
  The RTL simulation models 224 ps per stage; real per-stage delay is read
  from the calibration source on silicon.
* The Manchester transmitter streams a complete frame (preamble, SFD, up to
  128 payload bytes from the host FIFO, hardware CRC-32 FCS) at 10 Mbit/s;
  the receiver decodes a stream into the trace buffer and the host checks
  the FCS.  Frames longer than the FIFO need the host to refill it while
  the engine drains it, which the SPI link cannot sustain at line rate; the
  chip does not drive an MDI directly.
* The host link samples CFG_SCK with the 40 MHz core clock and needs
  SCK <= 5 MHz.
