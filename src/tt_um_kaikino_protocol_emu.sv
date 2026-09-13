/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Top level for the programmable protocol emulator.
 */
`default_nettype none

module tt_um_kaikino_protocol_emu (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  localparam [3:0] CMD_GPIO = 4'h1;
  localparam [3:0] CMD_PROGRAM = 4'h2;
  localparam [3:0] CMD_RUN = 4'h3;

  reg alive;
  reg [7:0] gpio_data;
  reg [7:0] gpio_oe_reg;
  wire [31:0] cfg_word;
  wire cfg_req_toggle;
  wire cfg_miso;
  reg cfg_sync_0;
  reg cfg_sync_1;
  reg cfg_req_seen;
  wire cfg_request = cfg_sync_1 ^ cfg_req_seen;
  wire [15:0] prog0_instruction;
  wire [7:0] prog0_pc;
  reg prog0_we;
  reg [7:0] prog0_waddr;
  reg [15:0] prog0_wdata;
  reg pio0_start;
  reg pio0_stop;
  wire [7:0] pio0_data;
  wire [7:0] pio0_oe;
  wire pio0_running;
  wire pio0_trace;

  proto_program_ram program0 (
      .clk(clk), .raddr(prog0_pc), .rdata(prog0_instruction),
      .we(prog0_we), .waddr(prog0_waddr), .wdata(prog0_wdata)
  );
  proto_pio_engine pio0 (
      .clk(clk), .rst_n(rst_n), .start(pio0_start), .stop(pio0_stop),
      .instruction(prog0_instruction), .pc(prog0_pc), .pin_in(uio_in),
      .pin_out(pio0_data), .pin_oe(pio0_oe), .running(pio0_running),
      .trace_event(pio0_trace)
  );

  proto_cfg_serial cfg (
      .clk        (clk),
      .rst_n      (rst_n),
      .cfg_sck    (ui_in[0]),
      .cfg_mosi   (ui_in[1]),
      .cfg_cs_n   (ui_in[2]),
      .cfg_miso   (cfg_miso),
      .cmd_word   (cfg_word),
      .req_toggle (cfg_req_toggle),
      .status     ({6'b0, alive, (gpio_oe_reg != 8'b0)})
  );

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      alive <= 1'b0;
      gpio_data <= 8'b0;
      gpio_oe_reg <= 8'b0;
      cfg_sync_0 <= 1'b0;
      cfg_sync_1 <= 1'b0;
      cfg_req_seen <= 1'b0;
      prog0_we <= 1'b0;
      prog0_waddr <= 8'b0;
      prog0_wdata <= 16'b0;
      pio0_start <= 1'b0;
      pio0_stop <= 1'b0;
    end else begin
      alive <= 1'b1;
      cfg_sync_0 <= cfg_req_toggle;
      cfg_sync_1 <= cfg_sync_0;
      prog0_we <= 1'b0;
      pio0_start <= 1'b0;
      pio0_stop <= 1'b0;
      if (cfg_request) begin
        cfg_req_seen <= cfg_sync_1;
        if (cfg_word[31:28] == CMD_GPIO) begin
          gpio_data <= cfg_word[15:8];
          gpio_oe_reg <= cfg_word[7:0];
        end
        if (cfg_word[31:28] == CMD_PROGRAM && !cfg_word[27] && !pio0_running) begin
          prog0_we <= 1'b1;
          prog0_waddr <= cfg_word[26:19];
          prog0_wdata <= cfg_word[18:3];
        end
        if (cfg_word[31:28] == CMD_RUN) begin
          pio0_start <= cfg_word[0];
          pio0_stop <= cfg_word[1];
        end
      end
    end
  end

  assign uo_out[0] = cfg_miso;
  assign uo_out[6:1] = 6'b0;
  assign uo_out[7] = alive;
  assign uio_out = pio0_running ? pio0_data : gpio_data;
  assign uio_oe  = pio0_running ? pio0_oe : gpio_oe_reg;

  wire _unused = &{ena, ui_in[7:3], uio_in, 1'b0};
endmodule
