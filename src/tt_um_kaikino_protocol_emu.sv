`timescale 1ns / 1ps
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
    parameter integer TRACE_AW    = 5,
    parameter integer FIFO_AW     = 7,   // 128-byte host-to-engine data FIFO
    parameter integer TDLY_STAGES = 128  // delay-line length: > half a period at the fast corner, ~1 period typical
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
  localparam [3:0] CMD_FIFO     = 4'h9;  // [7:0] data [8] push [9] reset [10] FCS mode
  localparam [3:0] CMD_TIMING   = 4'hA;  // [27] channel [26] 0: TDC {[3:0] source, [8] trace} 1: DTC {[2:0] pin, [8] enable, [23:16] tap}
  localparam [3:0] CMD_CRC      = 4'hB;  // [27:26] 0 polynomial 1 initial value 2 final XOR; [25:24] byte lane; [7:0] byte
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

  // ------------------------------------------------------------------
  // Sub-clock timing: two TDC channels (edge arrival time within the
  // clock period) and two DTC channels (edge placement within the period).
  // Sources 0-12 are the target inputs in pins_raw order; 13 is a flop that
  // toggles on the falling clock edge, so every rising-edge sample sees an
  // edge exactly half a period (12.5 ns) old: the self-calibration reference.
  // ------------------------------------------------------------------
  reg  [3:0]  tdc_src [0:1];
  reg         tdc_trace_en [0:1];
  reg  [7:0]  tdc_fine [0:1];
  reg         tdc_level [0:1];
  reg         tdc_level_prev [0:1];
  reg  [2:0]  dtc_pin [0:1];
  reg         dtc_en [0:1];
  reg  [7:0]  dtc_tap [0:1];
  reg         cal_toggle;
  wire [7:0]  tdc_fine_now [0:1];
  wire        tdc_level_now [0:1];
  wire        tdc_edge [0:1];
  wire        dtc_out [0:1];
  wire        e_dtc_we [0:1];
  wire        e_dtc_ch [0:1];
  wire [7:0]  e_dtc_data [0:1];
  wire [17:0] tdc_word = {tdc_level[1], tdc_level[0], tdc_fine[1], tdc_fine[0]};

  // ------------------------------------------------------------------
  // Host-to-engine data FIFO with a programmable CRC unit.  Either engine
  // may pop; a simultaneous pop hands both the same byte.  In FCS mode every
  // data byte popped is folded into the CRC and, once the FIFO is empty, the
  // next four pops return the check value XORed with crc_xorout, least
  // significant byte first (Ethernet FCS wire order with the defaults).
  //
  // The CRC is the reflected (LSB-first) form with a host-programmable
  // polynomial, initial value and final XOR; a shorter CRC uses the low bits
  // (poly, init and xorout zero above its width).  Defaults are CRC-32.
  // ------------------------------------------------------------------
  reg  [7:0]  fifo_mem [0:(1 << FIFO_AW) - 1];
  reg  [FIFO_AW-1:0] fifo_rptr, fifo_wptr;
  reg  [FIFO_AW:0]   fifo_count;
  reg         fcs_mode, fcs_done;
  reg  [1:0]  fcs_idx;
  reg  [31:0] crc;
  reg  [31:0] crc_poly, crc_init_val, crc_xorout;
  wire        fifo_empty = (fifo_count == {(FIFO_AW+1){1'b0}});
  wire        fifo_full  = fifo_count[FIFO_AW];
  // Read asynchronously in every build: on the iCE40 this costs 1k logic
  // cells (the board has room), but a falling-edge block-RAM read would
  // leave the programmable CRC fold only half a 12 MHz cycle and miss timing.
  wire [7:0]  fifo_head  = fifo_mem[fifo_rptr];
  wire [31:0] fcs        = crc ^ crc_xorout;
  wire [7:0]  fcs_byte   = fcs[fcs_idx*8 +: 8];
  wire        fcs_avail  = fcs_mode & ~fcs_done;
  wire        fifo_valid = ~fifo_empty | fcs_avail;
  wire [7:0]  fifo_data  = ~fifo_empty ? fifo_head : (fcs_avail ? fcs_byte : 8'd0);
  wire        e_fifo_pop [0:1];
  wire        e_crc_init [0:1];
  wire        e_crc_update [0:1];
  wire        e_crc_bit_update [0:1];
  wire        e_crc_bit [0:1];
  wire [7:0]  e_crc_byte [0:1];
  wire        fifo_pop   = e_fifo_pop[0] | e_fifo_pop[1];
  wire        crc_init   = e_crc_init[0] | e_crc_init[1];
  wire        crc_update = e_crc_update[0] | e_crc_update[1];
  wire [7:0]  crc_in     = e_crc_update[0] ? e_crc_byte[0] : e_crc_byte[1];
  wire        crc_fold   = crc_update | (fifo_pop & ~fifo_empty & fcs_mode);
  wire [7:0]  crc_fold_byte = crc_update ? crc_in : fifo_head;
  wire        crc_bit_fold = e_crc_bit_update[0] | e_crc_bit_update[1];
  wire        crc_bit_in   = e_crc_bit_update[0] ? e_crc_bit[0] : e_crc_bit[1];
  // Fold requests are registered and applied one clock later, so the
  // instruction decode and the eight-level fold are not in the same cycle.
  // A CRCB or FCS-byte POP in the clock right after a fold sees the old value.
  reg         crc_fold_r, crc_bit_fold_r, crc_bit_r;
  reg  [7:0]  crc_fold_byte_r;

  // Reflected CRC step: one bit, or eight bit-steps for a byte, per clock.
  function automatic [31:0] crc_step(input [31:0] c, input bit_in, input [31:0] poly);
    reg [31:0] x;
    begin
      x = c ^ {31'd0, bit_in};
      crc_step = (x >> 1) ^ (x[0] ? poly : 32'd0);
    end
  endfunction
  function automatic [31:0] crc_byte_fold(input [31:0] c, input [7:0] d, input [31:0] poly);
    integer b;
    reg [31:0] x;
    begin
      x = c;
      for (b = 0; b < 8; b = b + 1) x = crc_step(x, d[b], poly);
      crc_byte_fold = x;
    end
  endfunction

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
          .fault_illegal(e_fault_illegal[g]),
          .fifo_data(fifo_data), .fifo_valid(fifo_valid), .fcs(fcs),
          .fifo_pop(e_fifo_pop[g]), .crc_init(e_crc_init[g]),
          .crc_update(e_crc_update[g]), .crc_byte(e_crc_byte[g]),
          .crc_bit_update(e_crc_bit_update[g]), .crc_bit(e_crc_bit[g]),
          .tdc_word(tdc_word), .dtc_we(e_dtc_we[g]), .dtc_ch(e_dtc_ch[g]), .dtc_data(e_dtc_data[g])
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

  genvar c;
  generate
    for (c = 0; c < 2; c = c + 1) begin : timing
      wire tdc_in = (tdc_src[c] == 4'd13) ? cal_toggle :
                    (tdc_src[c] <= 4'd12) ? pins_raw[tdc_src[c]] : 1'b0;
      proto_tdc #(.STAGES(TDLY_STAGES)) tdc (
          .clk(clk), .rst_n(rst_n), .in(tdc_in),
          .fine_now(tdc_fine_now[c]), .level_now(tdc_level_now[c])
      );
      assign tdc_edge[c] = tdc_level_now[c] ^ tdc_level_prev[c];
      // The DTC input is the arbitrated pin value, launched by the clock.
      proto_dtc #(.STAGES(TDLY_STAGES)) dtc (
          .in(uo_target[dtc_pin[c]]), .sel(dtc_tap[c]), .out(dtc_out[c])
      );
    end
  endgenerate

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
  wire want_tdc0  = capturing & tdc_trace_en[0] & tdc_edge[0];
  wire want_tdc1  = capturing & tdc_trace_en[1] & tdc_edge[1];
  wire trace_full = trace_count[TRACE_AW];
  wire trace_block = trace_full & stop_on_full;
  wire trace_want  = e_trace_we[0] | e_trace_we[1] | want_tdc0 | want_tdc1 | want_pin;
  wire trace_we    = trace_want & ~trace_block;
  // One entry per clock: engine 0, engine 1, TDC 0, TDC 1, then pin capture.
  wire trace_drop  = (trace_want & trace_block) |
                     (e_trace_we[0] & (e_trace_we[1] | want_tdc0 | want_tdc1 | want_pin)) |
                     (e_trace_we[1] & (want_tdc0 | want_tdc1 | want_pin)) |
                     (want_tdc0 & (want_tdc1 | want_pin)) |
                     (want_tdc1 & want_pin);
  // Kinds: 0 engine event {eng, data8}, 1 pin capture {pins13},
  //        2 timed edge {channel, level, fine8, 4'b0}.
  wire [31:0] trace_wdata = e_trace_we[0] ? {timestamp, 2'd0, 1'b0, 5'd0, e_trace_data[0]} :
                            e_trace_we[1] ? {timestamp, 2'd0, 1'b1, 5'd0, e_trace_data[1]} :
                            want_tdc0     ? {timestamp, 2'd2, 1'b0, tdc_level_now[0], tdc_fine_now[0], 4'd0} :
                            want_tdc1     ? {timestamp, 2'd2, 1'b1, tdc_level_now[1], tdc_fine_now[1], 4'd0} :
                                            {timestamp, 2'd1, 1'b0, pins_q};

  proto_trace_ram #(.DEPTH(TRACE_DEPTH), .AW(TRACE_AW)) trace_ram (
      .clk(clk), .we(trace_we), .waddr(trace_wptr), .wdata(trace_wdata),
      .raddr(trace_rptr), .rdata(trace_rdata)
  );

  // ------------------------------------------------------------------
  // Readback multiplexer (the link captures it while CFG_CS_N is high).
  // ------------------------------------------------------------------
  /* verilator lint_off WIDTHEXPAND */
  wire [5:0]  trace_entries = trace_count;   // zero-extended for the status word
  /* verilator lint_on WIDTHEXPAND */
  wire [31:0] status_word = {VERSION, 2'd0, trace_entries,
                             trace_full, trace_overflow, triggered, armed,
                             mbox_rx_pending[1], mbox_rx_pending[0], mbox_tx_valid[1], mbox_tx_valid[0],
                             e_fault_illegal[1], e_fault_illegal[0], fault_perm, fault_collision,
                             e_done[1], e_done[0], e_running[1], e_running[0]};
  wire [31:0] read_mux = (read_sel == 3'd0) ? status_word :
                         (read_sel == 3'd1) ? {pc[1], pc[0], mbox_rx[1], mbox_rx[0]} :
                         (read_sel == 3'd2) ? trace_rdata :
                         (read_sel == 3'd3) ? {{(9-FIFO_AW){1'b0}}, fifo_count, fcs_mode, trace_wptr, timestamp} :
                         (read_sel == 3'd5) ? {TDLY_STAGES[7:0], 6'd0, tdc_level[1], tdc_level[0], tdc_fine[1], tdc_fine[0]} :
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
      fifo_rptr <= {FIFO_AW{1'b0}}; fifo_wptr <= {FIFO_AW{1'b0}}; fifo_count <= {(FIFO_AW+1){1'b0}};
      fcs_mode <= 1'b0; fcs_done <= 1'b0; fcs_idx <= 2'd0; crc <= 32'hFFFFFFFF;
      crc_poly <= 32'hEDB88320; crc_init_val <= 32'hFFFFFFFF; crc_xorout <= 32'hFFFFFFFF;
      crc_fold_r <= 1'b0; crc_bit_fold_r <= 1'b0; crc_bit_r <= 1'b0; crc_fold_byte_r <= 8'd0;
      for (k = 0; k < 2; k = k + 1) begin
        tdc_src[k] <= 4'd15; tdc_trace_en[k] <= 1'b0; tdc_fine[k] <= 8'd0;
        tdc_level[k] <= 1'b0; tdc_level_prev[k] <= 1'b0;
        dtc_pin[k] <= 3'd0; dtc_en[k] <= 1'b0; dtc_tap[k] <= 8'd0;
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

      // Sub-clock timing bookkeeping.
      for (k = 0; k < 2; k = k + 1) begin
        tdc_level_prev[k] <= tdc_level_now[k];
        if (tdc_edge[k]) begin tdc_fine[k] <= tdc_fine_now[k]; tdc_level[k] <= tdc_level_now[k]; end
      end
      if (e_dtc_we[0])      dtc_tap[e_dtc_ch[0]] <= e_dtc_data[0];
      else if (e_dtc_we[1]) dtc_tap[e_dtc_ch[1]] <= e_dtc_data[1];

      // FIFO pop side and CRC.
      if (fifo_pop) begin
        if (!fifo_empty) begin
          fifo_rptr  <= fifo_rptr + 1'b1;
          fifo_count <= fifo_count - 1'b1;
        end else if (fcs_avail) begin
          fcs_idx <= fcs_idx + 2'd1;
          if (fcs_idx == 2'd3) fcs_done <= 1'b1;
        end
      end
      crc_fold_r <= crc_fold; crc_fold_byte_r <= crc_fold_byte;
      crc_bit_fold_r <= crc_bit_fold & ~crc_fold; crc_bit_r <= crc_bit_in;
      if (crc_fold_r)          crc <= crc_byte_fold(crc, crc_fold_byte_r, crc_poly);
      else if (crc_bit_fold_r) crc <= crc_step(crc, crc_bit_r, crc_poly);
      if (crc_init) begin crc <= crc_init_val; fcs_idx <= 2'd0; fcs_done <= 1'b0; end

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
          CMD_TIMING: begin
            if (!cfg_word[26]) begin
              tdc_src[cfg_word[27]]      <= cfg_word[3:0];
              tdc_trace_en[cfg_word[27]] <= cfg_word[8];
            end else begin
              dtc_pin[cfg_word[27]] <= cfg_word[2:0];
              dtc_en[cfg_word[27]]  <= cfg_word[8];
              dtc_tap[cfg_word[27]] <= cfg_word[23:16];
            end
          end
          CMD_CRC: begin
            case (cfg_word[27:26])
              2'd0: crc_poly[cfg_word[25:24]*8 +: 8]     <= cfg_word[7:0];
              2'd1: crc_init_val[cfg_word[25:24]*8 +: 8] <= cfg_word[7:0];
              2'd2: crc_xorout[cfg_word[25:24]*8 +: 8]   <= cfg_word[7:0];
              default: ;
            endcase
          end
          CMD_FIFO: begin
            fcs_mode <= cfg_word[10];
            if (cfg_word[9]) begin
              fifo_rptr <= {FIFO_AW{1'b0}}; fifo_wptr <= {FIFO_AW{1'b0}}; fifo_count <= {(FIFO_AW+1){1'b0}};
              crc <= crc_init_val; fcs_idx <= 2'd0; fcs_done <= 1'b0;
            end else if (cfg_word[8] && !fifo_full) begin
              fifo_wptr  <= fifo_wptr + 1'b1;
              // A pop in the same clock keeps the count unchanged.
              fifo_count <= fifo_count + 1'b1 - {{FIFO_AW{1'b0}}, (fifo_pop & ~fifo_empty)};
            end
          end
          default: ;
        endcase
      end
    end
  end

  always @(posedge clk) begin
    if (cfg_request && cmd == CMD_FIFO && cfg_word[8] && !cfg_word[9] && !fifo_full)
      fifo_mem[fifo_wptr] <= cfg_word[7:0];
  end

  // Calibration reference: toggles on the falling edge, so each rising-edge
  // sample of the TDC sees an edge exactly half a clock period old.  (The
  // formal build keeps a single clock polarity; no property depends on it.)
`ifdef FORMAL
  always @(posedge clk or negedge rst_n) begin
`else
  always @(negedge clk or negedge rst_n) begin
`endif
    if (!rst_n) cal_toggle <= 1'b0;
    else        cal_toggle <= ~cal_toggle;
  end

  // A DTC channel replaces its selected TARGET_OUT bit with the delayed copy.
  genvar b;
  generate
    for (b = 0; b < 7; b = b + 1) begin : uo_mux
      assign uo_out[b+1] = (dtc_en[0] && dtc_pin[0] == b) ? dtc_out[0] :
                           (dtc_en[1] && dtc_pin[1] == b) ? dtc_out[1] : uo_target[b];
    end
  endgenerate

`ifdef FORMAL
  // ------------------------------------------------------------------
  // Safety properties checked with SymbiYosys (formal/proto.sby).
  // ------------------------------------------------------------------
  reg f_past_valid;
  initial f_past_valid = 1'b0;
  initial assume(!rst_n);
  always @(posedge clk) begin
    f_past_valid <= 1'b1;
    if (f_past_valid) assume(rst_n);
    if (rst_n) begin
      // P1: a contested GPIO is never driven by anybody.
      assert((uio_oe & coll_uio) == 8'd0);
      // P2: a GPIO is only driven with the host's permission or by the host.
      assert((uio_oe & ~(perm_uio[0] | perm_uio[1] | host_uio_oe)) == 8'd0);
      // P3: an engine that is not running never enables an output.
      assert(e_running[0] || (e_uio_oe[0] == 8'd0 && e_uo_oe[0] == 7'd0));
      assert(e_running[1] || (e_uio_oe[1] == 8'd0 && e_uo_oe[1] == 7'd0));
      // P4: the capture and FIFO bookkeeping never exceed their buffers.
      assert(trace_count <= TRACE_DEPTH);
      assert(fifo_count <= (1 << FIFO_AW));
      // P5: program memory is only written while the engine is stopped.
      assert(!prog_we[0] || !e_running[0] || eng_stop[0] || $past(!e_running[0]));
      assert(!prog_we[1] || !e_running[1] || eng_stop[1] || $past(!e_running[1]));
      if (f_past_valid && $past(rst_n)) begin
        // P6: a drive collision latches the sticky fault on the next clock
        //     unless that same clock carried the host's clear request.
        if ($past(|coll_uio || |coll_uo) &&
            !$past(cfg_request && cmd == CMD_RUN && cfg_word[4]))
          assert(fault_collision);
        // P7: a program write can only follow a PROGRAM frame.
        if (prog_we[0] || prog_we[1])
          assert($past(cfg_request && cmd == CMD_PROGRAM));
        // P8: the timestamp is free running unless the host resets it.
        if (!$past(cfg_request && cmd == CMD_RUN && cfg_word[5]))
          assert(timestamp == $past(timestamp) + 16'd1);
      end
    end
  end
`endif

  wire _unused = &{ena, CMD_NOP, cfg_word[18:11], prog_waddr[7:PROG_AW], pc[0][7:PROG_AW], pc[1][7:PROG_AW], 1'b0};
endmodule
