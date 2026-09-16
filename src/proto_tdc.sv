`timescale 1ns / 1ps
`default_nettype none

// Time-to-digital converter channel.  The selected input feeds a delay
// chain whose taps are sampled on every clock; the number of taps that
// already carry the input's new level says how many stages ago the last
// edge happened, i.e. its arrival time within the clock period at a
// resolution of one stage.  Counting ones is inherently tolerant of the
// "bubbles" that metastable taps produce.
//
// Timing of the outputs relative to the top level's input synchronisers:
// `snap` is stage 1 (like pins_s1), `snap_q` stage 2 (like pins_q), so
// `fine_now` describes the same sample that pins_q currently shows.
module proto_tdc #(
    parameter integer STAGES = 176
) (
    input  wire              clk,
    input  wire              rst_n,
    input  wire              in,        // raw (unsynchronised) selected input
    output wire [7:0]        fine_now,  // stages since the last edge of the current sample
    output wire              level_now  // level of that sample
);
  wire [STAGES:0] tap;
  proto_delay_chain #(.STAGES(STAGES)) chain (.in(in), .tdly_tap(tap));

  reg [STAGES-1:0] snap, snap_q;
  reg              lvl, lvl_q;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      snap <= {STAGES{1'b0}}; snap_q <= {STAGES{1'b0}};
      lvl <= 1'b0; lvl_q <= 1'b0;
    end else begin
      snap <= tap[STAGES:1]; snap_q <= snap;   // two flops: async sample, then settle
      lvl  <= tap[0];        lvl_q  <= lvl;
    end
  end

  // Taps that match the current level were reached by the edge; count them.
  wire [STAGES-1:0] matched = lvl_q ? snap_q : ~snap_q;
  integer k;
  reg [7:0] cnt;
  always @(*) begin
    cnt = 8'd0;
    for (k = 0; k < STAGES; k = k + 1) cnt = cnt + {7'd0, matched[k]};
  end
  assign fine_now  = cnt;
  assign level_now = lvl_q;
endmodule
