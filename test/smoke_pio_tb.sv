`timescale 1ns/1ps
// Engine smoke test: load a short program, run it, check pin drive, the
// trace entry and that HALT releases the bus.
module smoke_pio_tb;
  reg clk = 0, rst_n = 1, ena = 1;
  reg [7:0] ui_in = 8'b0000_0100, uio_in = 0;
  wire [7:0] uo_out, uio_out, uio_oe;
  tt_um_kaikino_protocol_emu dut (.*);
  always #12.5 clk = ~clk;
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
  task load(input eng, input [7:0] addr, input [15:0] instruction);
    xfer({4'h2, eng, addr, instruction, 3'b0});
  endtask
  initial begin
    #2 rst_n = 0; #50 rst_n = 1; #100;
    load(0, 0, 16'h10a5); // movi r0, 0xa5
    load(0, 1, 16'h14ff); // movi r1, 0xff
    load(0, 2, 16'h25ff); // out r1, UIO_OE, 0xff
    load(0, 3, 16'h20ff); // out r0, UIO, 0xff
    load(0, 4, 16'hd042); // trace 0x42
    load(0, 5, 16'h40ff); // delay 255
    load(0, 6, 16'he000); // halt
    xfer({4'h3, 4'b0, 8'd0, 15'b0, 1'b1});   // start engine 0 at pc 0
    #300;
    if (uio_out !== 8'ha5 || uio_oe !== 8'hff)
      $fatal(1, "PIO did not drive: out=%h oe=%h pc=%h", uio_out, uio_oe, dut.pc[0]);
    #7000;
    if (uio_oe !== 8'h00) $fatal(1, "halt did not release pins");
    xfer({4'h4, 28'd0});                   // trace pointer 0
    xfer({4'h8, 25'b0, 3'd2});             // select TRACE
    xfer(32'h0);
    if (rx[15:0] !== 16'h0042) $fatal(1, "trace entry %h", rx);
    xfer({4'h8, 25'b0, 3'd0});             // STATUS: one entry, nothing running
    xfer(32'h0);
    if (rx[21:16] !== 6'd1 || rx[1:0] !== 2'b00) $fatal(1, "status %h", rx);
    $display("PASS"); $finish;
  end
endmodule
