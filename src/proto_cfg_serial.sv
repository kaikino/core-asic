`default_nettype none

// A deliberately small, write-only configuration transport.  Frames are
// 32 bits, MSB first: {opcode[3:0], argument[27:0]}.  A completed frame is
// held stable until the next frame; req_toggle transfers that fact into clk.
module proto_cfg_serial (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        cfg_sck,
    input  wire        cfg_mosi,
    input  wire        cfg_cs_n,
    output wire        cfg_miso,
    output reg [31:0] cmd_word,
    output reg         req_toggle,
    input  wire [7:0]  status
);
  reg [31:0] shift_reg;
  reg [5:0]  bit_count;

  always @(posedge cfg_sck or negedge rst_n) begin
    if (!rst_n) begin
      shift_reg  <= 32'b0;
      bit_count  <= 6'b0;
      cmd_word   <= 32'b0;
      req_toggle <= 1'b0;
    end else if (cfg_cs_n) begin
      bit_count <= 6'b0;
    end else begin
      shift_reg <= {shift_reg[30:0], cfg_mosi};
      if (bit_count == 6'd31) begin
        cmd_word   <= {shift_reg[30:0], cfg_mosi};
        req_toggle <= ~req_toggle;
        bit_count  <= 6'b0;
      end else begin
        bit_count <= bit_count + 1'b1;
      end
    end
  end

  // Status is deliberately simple in this first control-plane revision: the
  // high bit is visible on MISO for host polling without another CDC path.
  assign cfg_miso = status[7];
  wire _unused = &{clk, 1'b0};
endmodule
