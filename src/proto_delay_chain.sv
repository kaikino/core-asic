`timescale 1ns / 1ps
`default_nettype none

// Tapped delay line built from CMOS5L delay cells.  tap[0] is the input,
// tap[i] the input delayed by i stages (about 0.15 / 0.22 / 0.35 ns per
// stage at the fast / typical / slow corner).
//
// Every instance and net is named with the token "tdly" so the flow's
// RSZ_DONT_TOUCH_RX (".*tdly.*" in src/config.json) keeps the resizer from
// buffering or deleting the chain, and src/proto.sdc declares the paths
// through it as false paths.  Without those two settings timing repair
// removes most of the cells (see experiments/tdc/README.md).
//
// The cell models are zero-delay, so simulation needs a substitute:
//   -DTDLY_PS=224   behavioural chain with that many picoseconds per stage
//                   (Icarus, cocotb; needs `timescale 1ns/1ps)
//   SYNTH / FORMAL  chain collapsed to zero delay (no such cells on iCE40;
//                   formal only needs the surrounding logic)
module proto_delay_chain #(
    parameter integer STAGES = 128
) (
    input  wire              in,
    output wire [STAGES:0]   tdly_tap
);
  assign tdly_tap[0] = in;
`ifdef TDLY_PS
  genvar i;
  generate
    for (i = 0; i < STAGES; i = i + 1) begin : tdly
      assign #(`TDLY_PS / 1000.0) tdly_tap[i+1] = tdly_tap[i];
    end
  endgenerate
`elsif SYNTH
  assign tdly_tap[STAGES:1] = {STAGES{in}};
`elsif FORMAL
  assign tdly_tap[STAGES:1] = {STAGES{in}};
`else
  genvar i;
  generate
    for (i = 0; i < STAGES; i = i + 1) begin : tdly
      (* keep *) sg13cmos5l_dlygate4sd2_1 tdly_cell (.A(tdly_tap[i]), .X(tdly_tap[i+1]));
    end
  endgenerate
`endif
endmodule
