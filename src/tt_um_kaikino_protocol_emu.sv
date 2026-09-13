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
    end else begin
      alive <= 1'b1;
      cfg_sync_0 <= cfg_req_toggle;
      cfg_sync_1 <= cfg_sync_0;
      if (cfg_request) begin
        cfg_req_seen <= cfg_sync_1;
        if (cfg_word[31:28] == CMD_GPIO) begin
          gpio_data <= cfg_word[15:8];
          gpio_oe_reg <= cfg_word[7:0];
        end
      end
    end
  end

  assign uo_out[0] = cfg_miso;
  assign uo_out[6:1] = 6'b0;
  assign uo_out[7] = alive;
  assign uio_out = gpio_data;
  assign uio_oe  = gpio_oe_reg;

  wire _unused = &{ena, ui_in[7:3], uio_in, 1'b0};
endmodule
