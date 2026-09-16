`timescale 1ns / 1ps
`default_nettype none

// Instruction memory wrapper.  The CMOS5L slim PDK used for this shuttle ships
// no SRAM macro, so the array is built from standard cells; the interface is
// kept SRAM-shaped (one write port, one asynchronous read port) so a macro
// can be dropped in without touching the engine or the control plane.
module proto_program_ram #(
    parameter integer DEPTH = 256,
    parameter integer AW    = 8
) (
    input  wire          clk,
    input  wire [AW-1:0] raddr,
    output wire [15:0]   rdata,
    input  wire          we,
    input  wire [AW-1:0] waddr,
    input  wire [15:0]   wdata
);
  reg [15:0] mem [0:DEPTH-1];
  always @(posedge clk) begin
    if (we) mem[waddr] <= wdata;
  end
`ifdef SYNTH
  // iCE40 build (tt_fpga.py defines SYNTH): read on the falling edge so the
  // array maps to block RAM yet still looks asynchronous to the rising-edge
  // logic, which keeps the FPGA cycle-for-cycle identical to the ASIC.
  reg [15:0] rdata_q;
  always @(negedge clk) rdata_q <= mem[raddr];
  assign rdata = rdata_q;
`else
  assign rdata = mem[raddr];
`endif
endmodule
