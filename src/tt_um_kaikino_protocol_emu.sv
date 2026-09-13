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
  localparam [3:0] CMD_TRACE_READ = 4'h4;

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
  wire [15:0] prog1_instruction;
  wire [7:0] prog1_pc;
  reg prog1_we;
  reg [7:0] prog1_waddr;
  reg [15:0] prog1_wdata;
  reg pio1_start, pio1_stop;
  wire [7:0] pio1_data, pio1_oe;
  wire pio1_running, pio1_trace;
  reg collision_fault;
  reg [7:0] timestamp;
  reg [4:0] trace_write_ptr, trace_read_ptr;
  wire [15:0] trace_read_data;
  wire trace_we = pio0_trace | pio1_trace;
  wire [15:0] trace_write_data = pio1_trace ? {timestamp, 1'b1, pio1_data[6:0]} : {timestamp, 1'b0, pio0_data[6:0]};
  wire [7:0] drive_collision = pio0_oe & pio1_oe;

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
  proto_program_ram program1 (.clk(clk), .raddr(prog1_pc), .rdata(prog1_instruction), .we(prog1_we), .waddr(prog1_waddr), .wdata(prog1_wdata));
  proto_pio_engine pio1 (
      .clk(clk), .rst_n(rst_n), .start(pio1_start), .stop(pio1_stop),
      .instruction(prog1_instruction), .pc(prog1_pc), .pin_in(uio_in),
      .pin_out(pio1_data), .pin_oe(pio1_oe), .running(pio1_running), .trace_event(pio1_trace)
  );
  proto_trace_ram trace_ram (.clk(clk), .we(trace_we), .waddr(trace_write_ptr),
      .wdata(trace_write_data), .raddr(trace_read_ptr), .rdata(trace_read_data));

  proto_cfg_serial cfg (
      .clk        (clk),
      .rst_n      (rst_n),
      .cfg_sck    (ui_in[0]),
      .cfg_mosi   (ui_in[1]),
      .cfg_cs_n   (ui_in[2]),
      .cfg_miso   (cfg_miso),
      .cmd_word   (cfg_word),
      .req_toggle (cfg_req_toggle),
      .status_high(collision_fault)
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
      prog1_we <= 1'b0; prog1_waddr <= 8'b0; prog1_wdata <= 16'b0;
      pio1_start <= 1'b0; pio1_stop <= 1'b0;
      collision_fault <= 1'b0;
      timestamp <= 8'b0; trace_write_ptr <= 5'b0; trace_read_ptr <= 5'b0;
    end else begin
      alive <= 1'b1;
      cfg_sync_0 <= cfg_req_toggle;
      cfg_sync_1 <= cfg_sync_0;
      prog0_we <= 1'b0;
      pio0_start <= 1'b0;
      pio0_stop <= 1'b0;
      prog1_we <= 1'b0; pio1_start <= 1'b0; pio1_stop <= 1'b0;
      if (drive_collision != 0) collision_fault <= 1'b1;
      timestamp <= timestamp + 1'b1;
      if (trace_we) trace_write_ptr <= trace_write_ptr + 1'b1;
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
        if (cfg_word[31:28] == CMD_PROGRAM && cfg_word[27] && !pio1_running) begin
          prog1_we <= 1'b1;
          prog1_waddr <= cfg_word[26:19];
          prog1_wdata <= cfg_word[18:3];
        end
        if (cfg_word[31:28] == CMD_RUN) begin
          pio0_start <= cfg_word[0];
          pio0_stop <= cfg_word[1];
          pio1_start <= cfg_word[2];
          pio1_stop <= cfg_word[3];
        end
        if (cfg_word[31:28] == CMD_TRACE_READ)
          trace_read_ptr <= cfg_word[4:0];
      end
    end
  end

  assign uo_out[0] = cfg_miso;
  // Trace readback is intentionally parallel so that all serial control pins
  // remain dedicated to loading programs. Read an entry with CMD_TRACE_READ.
  assign uo_out[6:1] = trace_read_data[5:0];
  assign uo_out[7] = alive;
  // A contested pin is made high impedance; unlike a wired-OR merge this is
  // safe for push-pull protocols and leaves a sticky diagnostic behind.
  assign uio_out = (pio0_running || pio1_running) ?
                   ((pio0_data & pio0_oe & ~pio1_oe) | (pio1_data & pio1_oe & ~pio0_oe)) : gpio_data;
  assign uio_oe  = (pio0_running || pio1_running) ? ((pio0_oe ^ pio1_oe) & ~drive_collision) : gpio_oe_reg;

  wire _unused = &{ena, alive, ui_in[7:3], uio_in, trace_read_data[15:6], 1'b0};
endmodule
