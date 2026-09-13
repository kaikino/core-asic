`default_nettype none

// Host control link: SPI mode 0 slave (CPOL=0, CPHA=0), 32-bit frames, MSB
// first, fully synchronous to the core clock.  CFG_SCK, CFG_MOSI and CFG_CS_N
// are oversampled through two-flop synchronisers, so the design has a single
// clock domain; CFG_SCK must stay below clk/8 (5 MHz at a 40 MHz core clock).
//
//   MOSI carries {opcode[3:0], argument[27:0]}.  cmd_valid pulses for one
//   clock after the 32nd rising SCK edge with the frame in cmd_word.
//   MISO returns read_data during every frame.  It is captured while CS is
//   high, so a frame always reads the register selected by earlier frames.
module proto_cfg_serial (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        cfg_sck,
    input  wire        cfg_mosi,
    input  wire        cfg_cs_n,
    output wire        cfg_miso,
    input  wire [31:0] read_data,
    output reg  [31:0] cmd_word,
    output reg         cmd_valid
);
  reg [2:0]  sck_sync;
  reg [1:0]  mosi_sync;
  reg [1:0]  cs_sync;
  reg [30:0] shift_in;
  reg [4:0]  bit_count;
  reg [31:0] shift_out;

  wire sck_rise = sck_sync[1] & ~sck_sync[2];
  wire sck_fall = ~sck_sync[1] & sck_sync[2];
  wire selected = ~cs_sync[1];

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      sck_sync  <= 3'b000;
      mosi_sync <= 2'b00;
      cs_sync   <= 2'b11;
      shift_in  <= 31'd0;
      bit_count <= 5'd0;
      shift_out <= 32'd0;
      cmd_word  <= 32'd0;
      cmd_valid <= 1'b0;
    end else begin
      sck_sync  <= {sck_sync[1:0], cfg_sck};
      mosi_sync <= {mosi_sync[0], cfg_mosi};
      cs_sync   <= {cs_sync[0], cfg_cs_n};
      cmd_valid <= 1'b0;
      if (!selected) begin
        bit_count <= 5'd0;
        shift_out <= read_data;
      end else begin
        if (sck_rise) begin
          shift_in  <= {shift_in[29:0], mosi_sync[1]};
          bit_count <= bit_count + 5'd1;
          if (bit_count == 5'd31) begin
            cmd_word  <= {shift_in, mosi_sync[1]};
            cmd_valid <= 1'b1;
          end
        end
        if (sck_fall) shift_out <= {shift_out[30:0], 1'b0};
      end
    end
  end

  assign cfg_miso = shift_out[31];
endmodule
