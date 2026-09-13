`default_nettype none

// Logical instruction SRAM interface.  The array is deliberately isolated so
// it can be replaced by the contest-approved IHP SRAM macro at hardening time
// without changing the engine or control-plane interface.
module proto_program_ram (
    input  wire        clk,
    input  wire [7:0]  raddr,
    output wire [15:0] rdata,
    input  wire        we,
    input  wire [7:0]  waddr,
    input  wire [15:0] wdata
);
  (* ram_style = "block" *) reg [15:0] mem [0:255];
  always @(posedge clk) begin
    if (we)
      mem[waddr] <= wdata;
  end
  assign rdata = mem[raddr];
endmodule
