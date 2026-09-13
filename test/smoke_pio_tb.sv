`timescale 1ns/1ps
module smoke_pio_tb;
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
      ui_in[2] = 1; #20;
    end
  endtask
  task load(input [7:0] addr, input [15:0] instruction);
    send_word({4'h2, 1'b0, addr, instruction, 3'b0});
  endtask
  initial begin
    #2 rst_n = 0; #12 rst_n = 1; #20;
    load(0, 16'h10a5); // MOVI r0, 0xa5
    load(1, 16'h14ff); // MOVI r1, 0xff
    load(2, 16'h2000); // WRITE r0/r1
    load(3, 16'h40ff); // DELAY 255 cycles
    load(4, 16'h8000); // HALT (and release pins)
    send_word({4'h3, 27'b0, 1'b1});
    #70;
    if (uio_out !== 8'ha5 || uio_oe !== 8'hff) begin
      $display("out=%h oe=%h running=%b pc=%h i0=%h i1=%h i2=%h", uio_out, uio_oe, dut.pio0_running, dut.prog0_pc, dut.program0.mem[0], dut.program0.mem[1], dut.program0.mem[2]);
      $fatal(1, "PIO did not drive loaded program");
    end
    #2600;
    if (uio_oe !== 8'h00) $fatal(1, "PIO halt did not release pins");
    $display("PASS"); $finish;
  end
endmodule
