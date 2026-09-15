`default_nettype none
// Feasibility probe for sub-clock timing on CMOS5L: a chain of explicitly
// instantiated delay cells tapped into a flop bank (a time-to-digital
// converter) plus a 4-way tap selector driving an output (a digital-to-time
// converter).  The point of the experiment is to see whether synthesis and
// place-and-route preserve the chain and what per-stage delay STA reports.
module tdc_probe #(
    parameter integer STAGES = 64
) (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        edge_in,      // asynchronous input edge to be timed
    input  wire [5:0]  tap_sel,      // which tap drives edge_out
    output reg  [5:0]  ones_count,   // thermometer code, counted
    output wire        edge_out
);
  wire [STAGES:0] dlytap;   // 'dly' in every chain net and instance name: matched by RSZ_DONT_TOUCH_RX
  assign dlytap[0] = edge_in;

  genvar i;
  generate
    for (i = 0; i < STAGES; i = i + 1) begin : chain
      (* keep *) sg13cmos5l_dlygate4sd2_1 dly (.A(dlytap[i]), .X(dlytap[i+1]));
    end
  endgenerate

  // Sample every tap on the clock edge: the number of ones tells how far
  // the input edge travelled since it arrived, i.e. its sub-cycle arrival time.
  reg [STAGES-1:0] snap;
  integer k;
  reg [5:0] cnt;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      snap <= {STAGES{1'b0}};
      ones_count <= 6'd0;
    end else begin
      snap <= dlytap[STAGES:1];
      cnt = 6'd0;
      for (k = 0; k < STAGES; k = k + 1) cnt = cnt + {5'd0, snap[k]};
      ones_count <= cnt;
    end
  end

  // Digital-to-time: pick one of 64 taps as the output edge.
  assign edge_out = dlytap[tap_sel + 1];
endmodule
