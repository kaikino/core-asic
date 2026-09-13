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
  assign rdata = mem[raddr];
endmodule
