`default_nettype none

// Deterministic 16-bit PIO engine.  Every instruction retires in exactly one
// clock unless it is a DELAY stall or an unsatisfied WAIT, so a microprogram's
// pin timing is a pure function of the program and the sampled inputs.
//
// Encoding: [15:12] opcode, [11:10] rd/rs, [9:8] sub-field, [7:0] immediate.
//
//  0 NOP
//  1 MOVI  rd, imm8
//  2 OUT   rs, tgt, mask8      tgt = (tgt & ~mask) | (rs & mask)
//  3 IN    rd, src             0 UIO_IN, 1 AUX, 2 MBOX (pops), 3 UIO_DATA
//  4 DELAY mode, imm8|rs       0 imm, 1 rs, 2 imm<<4, 3 rs<<4 extra stall cycles
//  5 WAIT  cond, pin           0 LOW, 1 HIGH, 2 RISE, 3 FALL
//  6 JMP   addr8
//  7 BR    rd, cond, addr8     0 JNZ, 1 DJNZ, 2 JZ, 3 DJZ
//  8 JPH   pin, addr8          branch if pin is high
//  9 JPL   pin, addr8          branch if pin is low
//  A SETP  rs, tgt, bit, src   single-bit write.  imm[6:5] selects the source:
//                              0 imm[7]; 1 carry; 2 rotate rs left and write
//                              its MSB (SHOUT_MSB); 3 rotate rs right and write
//                              its LSB (SHOUT_LSB).  For sources 1-3 imm[7]
//                              inverts the written bit.  Rotation keeps a byte
//                              intact after eight writes, so a register can
//                              feed a repeating pattern with no reload.
//  B SHIFT rd, mode, pin       0 SHL, 1 SHR, 2 SHIN_LSB, 3 SHIN_MSB
//  C ALU   rd, rs, fn          fn = imm[3:0]: 0 MOV 1 ADD 2 SUB 3 AND 4 OR
//                              5 XOR 6 MOVC 7 NOT 8 CRCU (fold rd into the
//                              CRC-32) 9 CRCI (init CRC) 10 CRCB rd,k (rd =
//                              FCS byte imm[5:4]) 11 POP rd (rd = next data
//                              FIFO byte, C = byte was valid)
//  D EVT   sub, imm8|rs        0 TRACE imm, 1 TRACE rs, 2 MBOX <= rs, 3 DONE
//  E HALT                      stop and release every output enable
//  F (illegal)                 HALT plus a sticky fault
//
// Targets: 0 UIO_DATA, 1 UIO_OE, 2 UO_DATA, 3 UO_OE.
// Pins: 0-7 uio, 8-11 TARGET_IN0-3, 12 TRIGGER_IN, 13 mailbox-in valid,
//       14 mailbox-out pending, 15 peer engine running.
module proto_pio_engine (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,
    input  wire [7:0]  start_pc,
    input  wire        stop,
    input  wire        clear_done,
    input  wire [15:0] instruction,
    output wire [7:0]  pc,
    input  wire [15:0] pin_in,
    input  wire [15:0] pin_prev,
    input  wire [7:0]  mbox_in,
    output reg         mbox_in_take,
    output reg  [7:0]  mbox_out,
    output reg         mbox_out_we,
    output reg  [7:0]  uio_data,
    output reg  [7:0]  uio_oe,
    output reg  [6:0]  uo_data,
    output reg  [6:0]  uo_oe,
    output reg         running,
    output reg         done,
    output reg         trace_we,
    output reg  [7:0]  trace_data,
    output reg         fault_illegal,
    // Shared data FIFO and CRC-32 (owned by the top level, combinational ops).
    input  wire [7:0]  fifo_data,
    input  wire        fifo_valid,
    input  wire [31:0] fcs,
    output wire        fifo_pop,
    output wire        crc_init,
    output wire        crc_update,
    output wire [7:0]  crc_byte
);
  reg [7:0]  program_counter;
  reg [11:0] delay_count;
  reg [7:0]  regs [0:3];
  reg        carry;

  wire [3:0] opcode = instruction[15:12];
  wire [1:0] rd     = instruction[11:10];
  wire [1:0] sub    = instruction[9:8];
  wire [7:0] imm    = instruction[7:0];
  wire [7:0] rd_val = regs[rd];
  wire [7:0] rs_val = regs[sub];
  wire [3:0] pin_sel4 = imm[3:0];
  wire       pin_now  = pin_in[pin_sel4];
  wire       pin_was  = pin_prev[pin_sel4];
  wire       pin_hi8  = pin_in[instruction[11:8]];
  wire [7:0] aux = {pin_in[15], pin_in[14], pin_in[13], pin_in[12], pin_in[11:8]};
  wire [7:0] pc_next = program_counter + 8'd1;

  wire wait_ok = (rd == 2'd0) ? ~pin_now :
                 (rd == 2'd1) ?  pin_now :
                 (rd == 2'd2) ? (pin_now & ~pin_was) : (~pin_now & pin_was);

  wire [7:0] dec_val = rd_val - 8'd1;
  wire [8:0] add_res = {1'b0, rd_val} + {1'b0, rs_val};
  wire [8:0] sub_res = {1'b0, rd_val} - {1'b0, rs_val};

  wire [11:0] delay_val = (sub == 2'd0) ? {4'b0, imm} :
                          (sub == 2'd1) ? {4'b0, rd_val} :
                          (sub == 2'd2) ? {imm, 4'b0} : {rd_val, 4'b0};

  wire       execute   = running & (delay_count == 12'd0);
  wire       alu_op    = execute & (opcode == 4'hC);
  assign fifo_pop   = alu_op & (imm[3:0] == 4'd11);
  assign crc_init   = alu_op & (imm[3:0] == 4'd9);
  assign crc_update = alu_op & (imm[3:0] == 4'd8);
  assign crc_byte   = rd_val;
  wire [7:0] fcs_sel   = fcs[imm[5:4]*8 +: 8];

  wire [7:0] setp_mask = 8'd1 << imm[2:0];
  wire       setp_base = (imm[6:5] == 2'd1) ? carry :
                         (imm[6:5] == 2'd2) ? rd_val[7] : rd_val[0];
  wire       setp_val  = (imm[6:5] == 2'd0) ? imm[7] : (setp_base ^ imm[7]);

  assign pc = program_counter;

  integer i;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      program_counter <= 8'd0;
      delay_count     <= 12'd0;
      carry           <= 1'b0;
      running         <= 1'b0;
      done            <= 1'b0;
      uio_data        <= 8'd0;
      uio_oe          <= 8'd0;
      uo_data         <= 7'd0;
      uo_oe           <= 7'd0;
      trace_we        <= 1'b0;
      trace_data      <= 8'd0;
      mbox_in_take    <= 1'b0;
      mbox_out        <= 8'd0;
      mbox_out_we     <= 1'b0;
      fault_illegal   <= 1'b0;
      for (i = 0; i < 4; i = i + 1) regs[i] <= 8'd0;
    end else begin
      trace_we     <= 1'b0;
      mbox_in_take <= 1'b0;
      mbox_out_we  <= 1'b0;
      if (clear_done) done <= 1'b0;
      if (stop) begin
        running <= 1'b0;
        uio_oe  <= 8'd0;
        uo_oe   <= 7'd0;
      end else if (start) begin
        program_counter <= start_pc;
        delay_count     <= 12'd0;
        carry           <= 1'b0;
        running         <= 1'b1;
        done            <= 1'b0;
      end else if (running) begin
        if (delay_count != 12'd0) begin
          delay_count <= delay_count - 12'd1;
        end else begin
          case (opcode)
            4'h0: program_counter <= pc_next;
            4'h1: begin regs[rd] <= imm; program_counter <= pc_next; end
            4'h2: begin
              case (sub)
                2'd0: uio_data <= (uio_data & ~imm) | (rd_val & imm);
                2'd1: uio_oe   <= (uio_oe   & ~imm) | (rd_val & imm);
                2'd2: uo_data  <= (uo_data  & ~imm[6:0]) | (rd_val[6:0] & imm[6:0]);
                2'd3: uo_oe    <= (uo_oe    & ~imm[6:0]) | (rd_val[6:0] & imm[6:0]);
              endcase
              program_counter <= pc_next;
            end
            4'h3: begin
              case (sub)
                2'd0: regs[rd] <= pin_in[7:0];
                2'd1: regs[rd] <= aux;
                2'd2: begin regs[rd] <= mbox_in; mbox_in_take <= 1'b1; end
                2'd3: regs[rd] <= uio_data;
              endcase
              program_counter <= pc_next;
            end
            4'h4: begin delay_count <= delay_val; program_counter <= pc_next; end
            4'h5: if (wait_ok) program_counter <= pc_next;
            4'h6: program_counter <= imm;
            4'h7: begin
              case (sub)
                2'd0: program_counter <= (rd_val != 8'd0) ? imm : pc_next;
                2'd1: begin regs[rd] <= dec_val; program_counter <= (dec_val != 8'd0) ? imm : pc_next; end
                2'd2: program_counter <= (rd_val == 8'd0) ? imm : pc_next;
                2'd3: begin regs[rd] <= dec_val; program_counter <= (dec_val == 8'd0) ? imm : pc_next; end
              endcase
            end
            4'h8: program_counter <= pin_hi8 ? imm : pc_next;
            4'h9: program_counter <= pin_hi8 ? pc_next : imm;
            4'hA: begin
              case (sub)
                2'd0: uio_data <= setp_val ? (uio_data | setp_mask) : (uio_data & ~setp_mask);
                2'd1: uio_oe   <= setp_val ? (uio_oe   | setp_mask) : (uio_oe   & ~setp_mask);
                2'd2: uo_data  <= setp_val ? (uo_data  | setp_mask[6:0]) : (uo_data & ~setp_mask[6:0]);
                2'd3: uo_oe    <= setp_val ? (uo_oe    | setp_mask[6:0]) : (uo_oe   & ~setp_mask[6:0]);
              endcase
              if (imm[6:5] == 2'd2) begin regs[rd] <= {rd_val[6:0], rd_val[7]}; carry <= rd_val[7]; end
              if (imm[6:5] == 2'd3) begin regs[rd] <= {rd_val[0], rd_val[7:1]}; carry <= rd_val[0]; end
              program_counter <= pc_next;
            end
            4'hB: begin
              case (sub)
                2'd0: begin regs[rd] <= {rd_val[6:0], 1'b0};    carry <= rd_val[7]; end
                2'd1: begin regs[rd] <= {1'b0, rd_val[7:1]};    carry <= rd_val[0]; end
                2'd2: begin regs[rd] <= {pin_now, rd_val[7:1]}; carry <= rd_val[0]; end
                2'd3: begin regs[rd] <= {rd_val[6:0], pin_now}; carry <= rd_val[7]; end
              endcase
              program_counter <= pc_next;
            end
            4'hC: begin
              case (imm[3:0])
                4'd0: regs[rd] <= rs_val;
                4'd1: begin regs[rd] <= add_res[7:0]; carry <= add_res[8]; end
                4'd2: begin regs[rd] <= sub_res[7:0]; carry <= sub_res[8]; end
                4'd3: regs[rd] <= rd_val & rs_val;
                4'd4: regs[rd] <= rd_val | rs_val;
                4'd5: regs[rd] <= rd_val ^ rs_val;
                4'd6: regs[rd] <= {7'd0, carry};
                4'd7: regs[rd] <= ~rd_val;
                4'd10: regs[rd] <= fcs_sel;
                4'd11: begin regs[rd] <= fifo_data; carry <= fifo_valid; end
                default: ;  // 8 CRCU, 9 CRCI act through crc_update/crc_init
              endcase
              program_counter <= pc_next;
            end
            4'hD: begin
              case (sub)
                2'd0: begin trace_we <= 1'b1; trace_data <= imm; end
                2'd1: begin trace_we <= 1'b1; trace_data <= rd_val; end
                2'd2: begin mbox_out <= rd_val; mbox_out_we <= 1'b1; end
                2'd3: done <= 1'b1;
              endcase
              program_counter <= pc_next;
            end
            4'hE: begin running <= 1'b0; uio_oe <= 8'd0; uo_oe <= 7'd0; end
            default: begin
              running <= 1'b0; uio_oe <= 8'd0; uo_oe <= 7'd0; fault_illegal <= 1'b1;
            end
          endcase
        end
      end
    end
  end
endmodule
