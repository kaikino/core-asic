`timescale 1ns/1ps
module smoke_cfg_tb;
  reg clk = 0, rst_n = 1, ena = 1;
  reg [7:0] ui_in = 0, uio_in = 0;
  wire [7:0] uo_out, uio_out, uio_oe;
  tt_um_kaikino_protocol_emu dut (.*);
  always #5 clk = ~clk;
  task send_word(input [31:0] word);
    integer i;
    begin
      ui_in[2] = 0;
      for (i = 31; i >= 0; i = i - 1) begin
        ui_in[1] = word[i]; #3 ui_in[0] = 1; #3 ui_in[0] = 0;
      end
      ui_in[2] = 1;
    end
  endtask
  initial begin
    #2 rst_n = 0; #12 rst_n = 1; #20;
    send_word({4'h1, 12'b0, 8'ha5, 8'h0f});
    #40;
    if (uio_out !== 8'ha5 || uio_oe !== 8'h0f || uo_out[7] !== 1'b1) begin
      $display("got out=%h oe=%h alive=%b word=%h count=%d", uio_out, uio_oe, uo_out[7], dut.cfg_word, dut.cfg.bit_count);
      $fatal(1, "configuration write failed");
    end
    $display("PASS"); $finish;
  end
endmodule
