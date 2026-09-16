`timescale 1ns / 1ps
`default_nettype none

// Digital-to-time converter channel: delays a registered pin value by a
// programmable number of chain stages so its edges land at a chosen
// fraction of the clock period.  `sel` is 0..STAGES; 0 passes the input
// through the chain's first tap (one stage of fixed delay).
module proto_dtc #(
    parameter integer STAGES = 176
) (
    input  wire        in,       // clock-launched pin value
    input  wire [7:0]  sel,      // delay in stages
    output wire        out
);
  wire [STAGES:0] tap;
  proto_delay_chain #(.STAGES(STAGES)) chain (.in(in), .tdly_tap(tap));
  localparam [7:0] LAST = STAGES[7:0];
  wire [7:0] idx = (sel > LAST) ? LAST : sel;
  assign out = tap[idx];
endmodule
