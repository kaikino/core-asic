`timescale 1ns/1ps
// Host-link smoke test: GPIO command, readback of the ID register, and a
// status read after a permission change.  Run: make -C test smoke
module smoke_cfg_tb;
  reg clk = 0, rst_n = 1, ena = 1;
  reg [7:0] ui_in = 8'b0000_0100, uio_in = 0;
  wire [7:0] uo_out, uio_out, uio_oe;
  tt_um_kaikino_protocol_emu dut (.*);
  always #12.5 clk = ~clk;                  // 40 MHz core clock
  reg [31:0] rx;
  task xfer(input [31:0] word);
    integer i;
    begin
      ui_in[2] = 0; #40;
      for (i = 31; i >= 0; i = i - 1) begin
        ui_in[1] = word[i]; #100; rx[i] = uo_out[0]; ui_in[0] = 1; #100; ui_in[0] = 0;
      end
      #40; ui_in[2] = 1; #200;
    end
  endtask
  initial begin
    #2 rst_n = 0; #50 rst_n = 1; #100;
    xfer({4'h1, 5'b0, 7'h55, 8'ha5, 8'h0f});          // host GPIO drive
    #40;
    if (uio_out !== 8'ha5 || uio_oe !== 8'h0f || uo_out[7:1] !== 7'h55)
      $fatal(1, "gpio write failed out=%h oe=%h uo=%h", uio_out, uio_oe, uo_out[7:1]);
    xfer({4'h8, 25'b0, 3'd4});                          // select ID register
    xfer(32'h0);                                        // NOP: returns ID
    if (rx !== 32'h50494f31) $fatal(1, "id readback %h", rx);
    xfer({4'h8, 25'b0, 3'd0});                          // select STATUS
    xfer(32'h0);
    if (rx[31:24] !== 8'h10 || rx[15:0] !== 16'h0) $fatal(1, "status readback %h", rx);
    $display("PASS"); $finish;
  end
endmodule
