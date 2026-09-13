`default_nettype none

// One cycle per instruction when not delayed or waiting. ISA:
// 0 NOP, 1 MOVI rd,imm8, 2 WRITE (r0=data,r1=oe), 3 SAMPLE rd,
// 4 DELAY imm8, 5 WAIT_HIGH mask8, 6 JMP addr8, 7 JNZ rd,addr8,
// 8 HALT, 9 TRACE (reserved observable event).
module proto_pio_engine (
    input  wire        clk,
    input  wire        rst_n,
    input  wire        start,
    input  wire        stop,
    input  wire [15:0] instruction,
    output wire [7:0]  pc,
    input  wire [7:0]  pin_in,
    output reg  [7:0]  pin_out,
    output reg  [7:0]  pin_oe,
    output reg         running,
    output reg         trace_event
);
  reg [7:0] program_counter;
  reg [7:0] delay_count;
  reg [7:0] regs [0:3];
  wire [3:0] opcode = instruction[15:12];
  integer i;

  assign pc = program_counter;
  wire _unused_instruction_bits = &{instruction[9:8], 1'b0};

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      program_counter <= 8'b0;
      delay_count <= 8'b0;
      pin_out <= 8'b0;
      pin_oe <= 8'b0;
      running <= 1'b0;
      trace_event <= 1'b0;
      for (i = 0; i < 4; i = i + 1)
        regs[i] <= 8'b0;
    end else begin
      trace_event <= 1'b0;
      if (stop) begin
        running <= 1'b0;
        pin_oe <= 8'b0;
      end else if (start) begin
        program_counter <= 8'b0;
        delay_count <= 8'b0;
        running <= 1'b1;
      end else if (running) begin
        if (delay_count != 0) begin
          delay_count <= delay_count - 1'b1;
        end else begin
          case (opcode)
            4'h0: program_counter <= program_counter + 1'b1;
            4'h1: begin
              regs[instruction[11:10]] <= instruction[7:0];
              program_counter <= program_counter + 1'b1;
            end
            4'h2: begin
              pin_out <= regs[0];
              pin_oe <= regs[1];
              program_counter <= program_counter + 1'b1;
            end
            4'h3: begin
              regs[instruction[11:10]] <= pin_in;
              program_counter <= program_counter + 1'b1;
            end
            4'h4: begin
              delay_count <= instruction[7:0];
              program_counter <= program_counter + 1'b1;
            end
            4'h5: if ((pin_in & instruction[7:0]) == instruction[7:0])
              program_counter <= program_counter + 1'b1;
            4'h6: program_counter <= instruction[7:0];
            4'h7: if (regs[instruction[11:10]] != 0)
              program_counter <= instruction[7:0];
            else
              program_counter <= program_counter + 1'b1;
            4'h8: begin
              running <= 1'b0;
              pin_oe <= 8'b0;
            end
            4'h9: begin
              trace_event <= 1'b1;
              program_counter <= program_counter + 1'b1;
            end
            default: begin
              running <= 1'b0;
              pin_oe <= 8'b0;
            end
          endcase
        end
      end
    end
  end
endmodule
