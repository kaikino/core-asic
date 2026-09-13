`default_nettype none

// Circular capture store.  Entry: {timestamp[15:0], kind[1:0], engine, data[12:0]}.
module proto_trace_ram #(
    parameter integer DEPTH = 32,
    parameter integer AW    = 5
) (
    input  wire          clk,
    input  wire          we,
    input  wire [AW-1:0] waddr,
    input  wire [31:0]   wdata,
    input  wire [AW-1:0] raddr,
    output wire [31:0]   rdata
);
  reg [31:0] mem [0:DEPTH-1];
  always @(posedge clk) begin
    if (we) mem[waddr] <= wdata;
  end
`ifdef SYNTH
  // iCE40 build (tt_fpga.py defines SYNTH): read on the falling edge so the
  // array maps to block RAM yet still looks asynchronous to the rising-edge
  // logic, which keeps the FPGA cycle-for-cycle identical to the ASIC.
  reg [31:0] rdata_q;
  always @(negedge clk) rdata_q <= mem[raddr];
  assign rdata = rdata_q;
`else
  assign rdata = mem[raddr];
`endif
endmodule
