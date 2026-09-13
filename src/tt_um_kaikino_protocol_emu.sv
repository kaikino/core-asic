/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Top level for the programmable protocol emulator.  The first milestone
 * keeps all target pins safe (inputs) and exposes a reset/status indication.
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
  reg alive;

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n)
      alive <= 1'b0;
    else
      alive <= 1'b1;
  end

  assign uo_out  = {7'b0, alive};
  assign uio_out = 8'b0;
  assign uio_oe  = 8'b0;

  wire _unused = &{ena, ui_in, uio_in, 1'b0};
endmodule
