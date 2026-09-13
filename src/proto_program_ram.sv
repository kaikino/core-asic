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
  assign rdata = mem[raddr];
endmodule
