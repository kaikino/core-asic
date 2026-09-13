/*
 * SPDX-License-Identifier: Apache-2.0
 *
 * Programmable protocol emulator: two PIO engines, a host control link,
 * per-engine pin permissions with collision protection, mailboxes, and a
 * timestamped trigger/capture buffer.
 *
 * Pins
 *   ui[0]  CFG_SCK      ui[1] CFG_MOSI    ui[2] CFG_CS_N   (host link)
 *   ui[3..6] TARGET_IN0..3 (sample only)  ui[7] TRIGGER_IN
 *   uo[0]  CFG_MISO     uo[1..7] TARGET_OUT0..6 (drive only)
 *   uio[0..7] GPIO0..7 (bidirectional)
 */
`default_nettype none

module tt_um_kaikino_protocol_emu #(
    parameter integer PROG_AW     = 7,   // 128 x 16 instruction words per engine
    parameter integer TRACE_DEPTH = 32,
    parameter integer TRACE_AW    = 5
) (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
  // ------------------------------------------------------------------
  // Host command set (frame[31:28]) and readback registers.
  // ------------------------------------------------------------------
  localparam [3:0] CMD_NOP      = 4'h0;
  localparam [3:0] CMD_GPIO     = 4'h1;  // [7:0] uio_oe [15:8] uio_data [22:16] uo_data
  localparam [3:0] CMD_PROGRAM  = 4'h2;  // [27] engine [26:19] addr (low PROG_AW bits used) [18:3] instruction
  localparam [3:0] CMD_RUN      = 4'h3;  // see decode below
  localparam [3:0] CMD_TRACEPTR = 4'h4;  // [TRACE_AW-1:0] read index
  localparam [3:0] CMD_PERM     = 4'h5;  // [27] engine [7:0] uio mask [14:8] uo mask
  localparam [3:0] CMD_MBOX     = 4'h6;  // [27] engine [7:0] data
  localparam [3:0] CMD_CAPTURE  = 4'h7;  // [12:0] watch [15:13] trigger [16] pin capture [17] stop on full
  localparam [3:0] CMD_READSEL  = 4'h8;  // [2:0] readback register
  localparam [7:0] VERSION      = 8'h10;

  // ------------------------------------------------------------------
  // Input synchronisation: 13 target inputs, two flops plus one history flop.
  // ------------------------------------------------------------------
  wire [12:0] pins_raw = {ui_in[7], ui_in[6:3], uio_in};
  reg  [12:0] pins_s1, pins_q, pins_qq;

  // ------------------------------------------------------------------
  // Control link.
  // ------------------------------------------------------------------
  wire [31:0] cfg_word;
  wire        cfg_request;
  wire [3:0]  cmd = cfg_word[31:28];
  reg  [2:0]  read_sel;

  // ------------------------------------------------------------------
  // Engines and instruction memories.
  // ------------------------------------------------------------------
  reg         prog_we [0:1];
  reg  [7:0]  prog_waddr;
  reg  [15:0] prog_wdata;
  reg         eng_start [0:1];
  reg         eng_stop [0:1];
  reg         eng_clear_done;
  reg  [7:0]  start_pc;
  wire [15:0] instr [0:1];
  wire [7:0]  pc [0:1];
  wire [7:0]  e_uio_data [0:1];
  wire [7:0]  e_uio_oe [0:1];
  wire [6:0]  e_uo_data [0:1];
  wire [6:0]  e_uo_oe [0:1];
  wire        e_running [0:1];
  wire        e_done [0:1];
  wire        e_trace_we [0:1];
  wire [7:0]  e_trace_data [0:1];
  wire        e_fault_illegal [0:1];
  wire        e_mbox_take [0:1];
  wire [7:0]  e_mbox_out [0:1];
  wire        e_mbox_out_we [0:1];

  reg  [7:0]  mbox_tx [0:1];
  reg         mbox_tx_valid [0:1];
  reg  [7:0]  mbox_rx [0:1];
  reg         mbox_rx_pending [0:1];
  reg  [7:0]  perm_uio [0:1];
  reg  [6:0]  perm_uo [0:1];

  reg  [7:0]  host_uio_data, host_uio_oe;
  reg  [6:0]  host_uo_data;

  reg         fault_collision, fault_perm;

  genvar g;
  generate
    for (g = 0; g < 2; g = g + 1) begin : eng
      wire [15:0] pin_in = {e_running[1-g], mbox_rx_pending[g], mbox_tx_valid[g], pins_q};
      wire [15:0] pin_prev = {e_running[1-g], mbox_rx_pending[g], mbox_tx_valid[g], pins_qq};
      proto_program_ram #(.DEPTH(1 << PROG_AW), .AW(PROG_AW)) imem (
          .clk(clk), .raddr(pc[g][PROG_AW-1:0]), .rdata(instr[g]),
          .we(prog_we[g]), .waddr(prog_waddr[PROG_AW-1:0]), .wdata(prog_wdata)
      );
      proto_pio_engine pio (
          .clk(clk), .rst_n(rst_n),
          .start(eng_start[g]), .start_pc(start_pc), .stop(eng_stop[g]),
          .clear_done(eng_clear_done),
          .instruction(instr[g]), .pc(pc[g]),
          .pin_in(pin_in), .pin_prev(pin_prev),
          .mbox_in(mbox_tx[g]), .mbox_in_take(e_mbox_take[g]),
          .mbox_out(e_mbox_out[g]), .mbox_out_we(e_mbox_out_we[g]),
          .uio_data(e_uio_data[g]), .uio_oe(e_uio_oe[g]),
          .uo_data(e_uo_data[g]), .uo_oe(e_uo_oe[g]),
          .running(e_running[g]), .done(e_done[g]),
          .trace_we(e_trace_we[g]), .trace_data(e_trace_data[g]),
          .fault_illegal(e_fault_illegal[g])
      );
    end
  endgenerate

  // ------------------------------------------------------------------
  // Safe pin arbitration.  An engine may only enable pins the host granted;
  // if both engines enable the same pin neither drives it and a fault latches.
  // ------------------------------------------------------------------
  wire [7:0] req_uio0 = e_uio_oe[0] & perm_uio[0];
  wire [7:0] req_uio1 = e_uio_oe[1] & perm_uio[1];
  wire [6:0] req_uo0  = e_uo_oe[0] & perm_uo[0];
  wire [6:0] req_uo1  = e_uo_oe[1] & perm_uo[1];
  wire [7:0] coll_uio = req_uio0 & req_uio1;
  wire [6:0] coll_uo  = req_uo0 & req_uo1;
  wire [7:0] drv_uio0 = req_uio0 & ~coll_uio;
  wire [7:0] drv_uio1 = req_uio1 & ~coll_uio;
  wire [6:0] drv_uo0  = req_uo0 & ~coll_uo;
  wire [6:0] drv_uo1  = req_uo1 & ~coll_uo;
  wire       perm_violation = |(e_uio_oe[0] & ~perm_uio[0]) | |(e_uio_oe[1] & ~perm_uio[1]) |
                              |(e_uo_oe[0] & ~perm_uo[0])   | |(e_uo_oe[1] & ~perm_uo[1]);

  assign uio_out = (drv_uio0 & e_uio_data[0]) | (drv_uio1 & e_uio_data[1]) |
                   (~(req_uio0 | req_uio1) & host_uio_data);
  assign uio_oe  = drv_uio0 | drv_uio1 | (~(req_uio0 | req_uio1) & host_uio_oe);
  wire [6:0] uo_target = (drv_uo0 & e_uo_data[0]) | (drv_uo1 & e_uo_data[1]) |
                         (~(req_uo0 | req_uo1) & host_uo_data);

  // ------------------------------------------------------------------
  // Timestamp, trigger and capture buffer.
  // ------------------------------------------------------------------
  reg  [15:0] timestamp;
  reg  [12:0] watch_mask;
  reg  [2:0]  trig_src;
  reg         pin_capture_en, stop_on_full;
  reg         armed, triggered, trace_overflow;
  reg  [TRACE_AW-1:0] trace_wptr, trace_rptr;
  reg  [TRACE_AW:0]   trace_count;
  wire [31:0] trace_rdata;

  wire [12:0] pin_change = (pins_q ^ pins_qq) & watch_mask;
  wire trig_rise = pins_q[12] & ~pins_qq[12];
  wire trig_fall = ~pins_q[12] & pins_qq[12];
  wire trig_hit  = (trig_src == 3'd0) |
                   (trig_src == 3'd1 && trig_rise) |
                   (trig_src == 3'd2 && trig_fall) |
                   (trig_src == 3'd3 && e_trace_we[0]) |
                   (trig_src == 3'd4 && e_trace_we[1]) |
                   (trig_src == 3'd5 && pin_change != 13'd0);
  wire capturing  = triggered | (armed & trig_hit);
  wire want_pin   = capturing & pin_capture_en & (pin_change != 13'd0);
  wire trace_full = trace_count[TRACE_AW];
  wire trace_block = trace_full & stop_on_full;
  wire trace_want  = e_trace_we[0] | e_trace_we[1] | want_pin;
  wire trace_we    = trace_want & ~trace_block;
  wire trace_drop  = (trace_want & trace_block) |
                     (e_trace_we[0] & (e_trace_we[1] | want_pin)) |
                     (e_trace_we[1] & want_pin);
  wire [31:0] trace_wdata = e_trace_we[0] ? {timestamp, 2'd0, 1'b0, 5'd0, e_trace_data[0]} :
                            e_trace_we[1] ? {timestamp, 2'd0, 1'b1, 5'd0, e_trace_data[1]} :
                                            {timestamp, 2'd1, 1'b0, pins_q};

  proto_trace_ram #(.DEPTH(TRACE_DEPTH), .AW(TRACE_AW)) trace_ram (
      .clk(clk), .we(trace_we), .waddr(trace_wptr), .wdata(trace_wdata),
      .raddr(trace_rptr), .rdata(trace_rdata)
  );

  // ------------------------------------------------------------------
  // Readback multiplexer (the link captures it while CFG_CS_N is high).
  // ------------------------------------------------------------------
  wire [31:0] status_word = {VERSION, 2'd0, trace_count,
                             trace_full, trace_overflow, triggered, armed,
                             mbox_rx_pending[1], mbox_rx_pending[0], mbox_tx_valid[1], mbox_tx_valid[0],
                             e_fault_illegal[1], e_fault_illegal[0], fault_perm, fault_collision,
                             e_done[1], e_done[0], e_running[1], e_running[0]};
  wire [31:0] read_mux = (read_sel == 3'd0) ? status_word :
                         (read_sel == 3'd1) ? {pc[1], pc[0], mbox_rx[1], mbox_rx[0]} :
                         (read_sel == 3'd2) ? trace_rdata :
                         (read_sel == 3'd3) ? {{(16-TRACE_AW){1'b0}}, trace_wptr, timestamp} :
                                              32'h50494F31;

  proto_cfg_serial cfg (
      .clk(clk), .rst_n(rst_n),
      .cfg_sck(ui_in[0]), .cfg_mosi(ui_in[1]), .cfg_cs_n(ui_in[2]),
      .cfg_miso(uo_out[0]), .read_data(read_mux),
      .cmd_word(cfg_word), .cmd_valid(cfg_request)
  );

  integer k;
  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
      pins_s1 <= 13'd0; pins_q <= 13'd0; pins_qq <= 13'd0;
      read_sel <= 3'd0;
      prog_waddr <= 8'd0; prog_wdata <= 16'd0; start_pc <= 8'd0;
      eng_clear_done <= 1'b0;
      host_uio_data <= 8'd0; host_uio_oe <= 8'd0; host_uo_data <= 7'd0;
      fault_collision <= 1'b0; fault_perm <= 1'b0;
      timestamp <= 16'd0; watch_mask <= 13'd0; trig_src <= 3'd0;
      pin_capture_en <= 1'b0; stop_on_full <= 1'b1;
      armed <= 1'b0; triggered <= 1'b0; trace_overflow <= 1'b0;
      trace_wptr <= {TRACE_AW{1'b0}}; trace_rptr <= {TRACE_AW{1'b0}}; trace_count <= {(TRACE_AW+1){1'b0}};
      for (k = 0; k < 2; k = k + 1) begin
        prog_we[k] <= 1'b0; eng_start[k] <= 1'b0; eng_stop[k] <= 1'b0;
        mbox_tx[k] <= 8'd0; mbox_tx_valid[k] <= 1'b0;
        mbox_rx[k] <= 8'd0; mbox_rx_pending[k] <= 1'b0;
        perm_uio[k] <= 8'hFF; perm_uo[k] <= 7'h7F;
      end
    end else begin
      pins_s1 <= pins_raw; pins_q <= pins_s1; pins_qq <= pins_q;

      timestamp <= timestamp + 16'd1;
      if (|coll_uio | |coll_uo) fault_collision <= 1'b1;
      if (perm_violation) fault_perm <= 1'b1;

      // Capture state machine.
      if (armed & trig_hit) begin armed <= 1'b0; triggered <= 1'b1; end
      if (trace_we) begin
        trace_wptr <= trace_wptr + 1'b1;
        if (!trace_full) trace_count <= trace_count + 1'b1;
        if (trace_wptr == {TRACE_AW{1'b1}}) trace_overflow <= 1'b1;
      end
      if (trace_drop) trace_overflow <= 1'b1;

      // Mailboxes.
      for (k = 0; k < 2; k = k + 1) begin
        if (e_mbox_take[k]) mbox_tx_valid[k] <= 1'b0;
        if (e_mbox_out_we[k]) begin mbox_rx[k] <= e_mbox_out[k]; mbox_rx_pending[k] <= 1'b1; end
        prog_we[k] <= 1'b0; eng_start[k] <= 1'b0; eng_stop[k] <= 1'b0;
      end
      eng_clear_done <= 1'b0;

      if (cfg_request) begin
        case (cmd)
          CMD_GPIO: begin
            host_uio_oe   <= cfg_word[7:0];
            host_uio_data <= cfg_word[15:8];
            host_uo_data  <= cfg_word[22:16];
          end
          CMD_PROGRAM: begin
            prog_waddr <= cfg_word[26:19];
            prog_wdata <= cfg_word[18:3];
            if (!cfg_word[27] && !e_running[0]) prog_we[0] <= 1'b1;
            if ( cfg_word[27] && !e_running[1]) prog_we[1] <= 1'b1;
          end
          CMD_RUN: begin
            start_pc     <= cfg_word[23:16];
            eng_start[0] <= cfg_word[0];
            eng_stop[0]  <= cfg_word[1];
            eng_start[1] <= cfg_word[2];
            eng_stop[1]  <= cfg_word[3];
            if (cfg_word[4]) begin fault_collision <= 1'b0; fault_perm <= 1'b0; end
            if (cfg_word[5]) timestamp <= 16'd0;
            if (cfg_word[6]) begin
              armed <= 1'b1; triggered <= 1'b0; trace_overflow <= 1'b0;
              trace_wptr <= {TRACE_AW{1'b0}}; trace_count <= {(TRACE_AW+1){1'b0}};
            end
            if (cfg_word[7]) begin armed <= 1'b0; triggered <= 1'b0; end
            if (cfg_word[8]) mbox_rx_pending[0] <= 1'b0;
            if (cfg_word[9]) mbox_rx_pending[1] <= 1'b0;
            eng_clear_done <= cfg_word[10];
          end
          CMD_TRACEPTR: trace_rptr <= cfg_word[TRACE_AW-1:0];
          CMD_PERM: begin
            perm_uio[cfg_word[27]] <= cfg_word[7:0];
            perm_uo[cfg_word[27]]  <= cfg_word[14:8];
          end
          CMD_MBOX: begin
            mbox_tx[cfg_word[27]]       <= cfg_word[7:0];
            mbox_tx_valid[cfg_word[27]] <= 1'b1;
          end
          CMD_CAPTURE: begin
            watch_mask     <= cfg_word[12:0];
            trig_src       <= cfg_word[15:13];
            pin_capture_en <= cfg_word[16];
            stop_on_full   <= cfg_word[17];
          end
          CMD_READSEL: read_sel <= cfg_word[2:0];
          default: ;
        endcase
      end
    end
  end

  assign uo_out[7:1] = uo_target;

  wire _unused = &{ena, CMD_NOP, cfg_word[27:24], cfg_word[18:0], prog_waddr[7:PROG_AW], pc[0][7:PROG_AW], pc[1][7:PROG_AW], 1'b0};
endmodule
