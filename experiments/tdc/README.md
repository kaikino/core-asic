# Delay-chain feasibility probe (sub-clock timing)

Question: will the CMOS5L LibreLane flow preserve a chain of explicitly
instantiated delay cells, and what per-stage delay do the timing tools report?
This is the go/no-go for the sub-clock timing extension (a time-to-digital
converter for measuring input edges and a digital-to-time converter for
placing output edges at a fraction of the 40 MHz clock period).

`tdc_probe.sv` is a 64-stage chain of `sg13cmos5l_dlygate4sd2_1` cells fed by
an asynchronous input, with every tap sampled into a flop bank (thermometer
code) and a 64-way tap selector driving an output.  `config.json` runs it
standalone on a 160 x 120 um die; `analyze.py` checks the result.

## Result (2026-09-16, LibreLane 3.0.0rc1, IHP CMOS5L)

| | |
|---|---|
| Delay cells after the full flow | 64 of 64, no buffers spliced into the chain |
| Routing DRC | 0 |
| Per-stage delay, slow corner (1.08 V, 125 C) | 353 ps |
| Per-stage delay, typical (1.20 V, 25 C) | 224 ps |
| Per-stage delay, fast (1.32 V, -40 C) | 150 ps |
| Stages to span one 25 ns period | 71 (slow) / 112 (typ) / 166 (fast) |
| Area of the 64-stage probe incl. flops and mux | 9 081 um2 |

So a ~170-stage chain (about 11 000 um2 with its flops, under 1 % of the 8x4
tile) spans a full clock period at every corner with 0.15-0.35 ns resolution.
The 2.3x corner spread means the design must self-calibrate: measuring how
many stages one clock period covers (the clock itself is a known 25 ns
reference) turns tap counts into nanoseconds.

## What made it work (first attempt failed)

1. `(* keep *)` on the instantiated cells: Yosys kept all 64 through
   synthesis in both attempts.
2. OpenROAD's post-CTS timing repair deleted 28 cells in the first attempt
   because the asynchronous input-to-flop path violated setup under the
   generic SDC.  Fix: a custom SDC (`tdc.sdc`, the LibreLane base SDC plus
   `set_max_delay 200 -from [get_ports edge_in]`, and a false path to the
   output) so the path is reported but never "repaired".  The final design
   will use false paths for the asynchronous inputs.
3. `RSZ_DONT_TOUCH_RX` must match the flattened instance and net names.
   Bracketed generate names (`chain[0].dly`) did not survive the regex
   escaping through the flow; naming every chain net and instance with a
   unique token (`dly`) and matching `.*dly.*` did.

## Still to settle before building the real thing

* Thermometer-code bubbles from metastable taps: use a bubble-tolerant
  encoder (count ones, as the probe does) and sample with two flop stages.
* The DTC output path is clock-launched (flop -> chain tap -> mux -> pad);
  it must be constrained as a normal path with the chain length bounded to
  one period at the fast corner.
* The Tiny Tapeout flow must accept `PNR_SDC_FILE` / `SIGNOFF_SDC_FILE`
  from `src/config.json`; verify on the full design.
* Calibration and measurement instructions in the ISA, and how the
  reference model represents sub-cycle time (it can only model tap counts
  given an assumed per-stage delay).
