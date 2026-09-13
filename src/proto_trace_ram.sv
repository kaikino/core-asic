`default_nettype none

// Small circular event store. Each entry is {timestamp[7:0], source, data[6:0]}.
module proto_trace_ram (
    input wire clk, input wire we, input wire [4:0] waddr,
    input wire [15:0] wdata, input wire [4:0] raddr, output wire [15:0] rdata
);
  reg [15:0] mem [0:31];
  always @(posedge clk) if (we) mem[waddr] <= wdata;
  assign rdata = mem[raddr];
endmodule
