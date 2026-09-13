#!/usr/bin/env python3
"""Cycle-accurate executable reference model of the protocol emulator.

`Engine.step()` mirrors one clock edge of src/proto_pio_engine.sv and
`Chip.step()` mirrors one clock edge of src/tt_um_kaikino_protocol_emu.sv,
including the input synchronisers, permission/collision arbitration, mailboxes,
timestamping and the trigger/capture buffer.  The verification suite compares
the RTL against this model pin-for-pin on every clock.
"""
from __future__ import annotations

from dataclasses import dataclass, field

TRACE_DEPTH = 32
PROG_DEPTH = 128  # words per engine; PC bit 7 is ignored by the memory
VERSION = 0x10


@dataclass
class EngineOut:
    uio_data: int = 0
    uio_oe: int = 0
    uo_data: int = 0
    uo_oe: int = 0


class Engine:
    def __init__(self) -> None:
        self.pc = 0
        self.delay = 0
        self.regs = [0, 0, 0, 0]
        self.carry = 0
        self.running = False
        self.done = False
        self.out = EngineOut()
        self.fault_illegal = False
        # one-cycle pulses visible to the top level after the edge
        self.trace_we = False
        self.trace_data = 0
        self.mbox_in_take = False
        self.mbox_out = 0
        self.mbox_out_we = False

    def step(self, instr: int, pin_in: int, pin_prev: int, mbox_in: int,
             start: bool, start_pc: int, stop: bool, clear_done: bool) -> None:
        self.trace_we = False
        self.mbox_in_take = False
        self.mbox_out_we = False
        if clear_done:
            self.done = False
        if stop:
            self.running = False
            self.out.uio_oe = 0
            self.out.uo_oe = 0
            return
        if start:
            self.pc = start_pc
            self.delay = 0
            self.carry = 0
            self.running = True
            self.done = False
            return
        if not self.running:
            return
        if self.delay:
            self.delay -= 1
            return
        self._execute(instr, pin_in, pin_prev, mbox_in)

    def _execute(self, w: int, pin_in: int, pin_prev: int, mbox_in: int) -> None:
        op, rd, sub, imm = w >> 12, (w >> 10) & 3, (w >> 8) & 3, w & 0xFF
        rd_val, rs_val = self.regs[rd], self.regs[sub]
        pin_now = (pin_in >> (imm & 15)) & 1
        pin_was = (pin_prev >> (imm & 15)) & 1
        pin_hi8 = (pin_in >> ((w >> 8) & 15)) & 1
        nxt = (self.pc + 1) & 0xFF
        o = self.out
        if op == 0x0:
            self.pc = nxt
        elif op == 0x1:
            self.regs[rd] = imm; self.pc = nxt
        elif op == 0x2:
            if sub == 0: o.uio_data = (o.uio_data & ~imm | rd_val & imm) & 0xFF
            elif sub == 1: o.uio_oe = (o.uio_oe & ~imm | rd_val & imm) & 0xFF
            elif sub == 2: o.uo_data = (o.uo_data & ~imm | rd_val & imm) & 0x7F
            else: o.uo_oe = (o.uo_oe & ~imm | rd_val & imm) & 0x7F
            self.pc = nxt
        elif op == 0x3:
            if sub == 0: self.regs[rd] = pin_in & 0xFF
            elif sub == 1: self.regs[rd] = (pin_in >> 8) & 0xFF
            elif sub == 2: self.regs[rd] = mbox_in; self.mbox_in_take = True
            else: self.regs[rd] = o.uio_data
            self.pc = nxt
        elif op == 0x4:
            base = rd_val if sub & 1 else imm
            self.delay = (base << 4) if sub & 2 else base
            self.pc = nxt
        elif op == 0x5:
            cond = rd
            ok = [not pin_now, pin_now, pin_now and not pin_was, (not pin_now) and pin_was][cond]
            if ok:
                self.pc = nxt
        elif op == 0x6:
            self.pc = imm
        elif op == 0x7:
            if sub == 0: self.pc = imm if rd_val != 0 else nxt
            elif sub == 1:
                d = (rd_val - 1) & 0xFF; self.regs[rd] = d; self.pc = imm if d != 0 else nxt
            elif sub == 2: self.pc = imm if rd_val == 0 else nxt
            else:
                d = (rd_val - 1) & 0xFF; self.regs[rd] = d; self.pc = imm if d == 0 else nxt
        elif op == 0x8:
            self.pc = imm if pin_hi8 else nxt
        elif op == 0x9:
            self.pc = nxt if pin_hi8 else imm
        elif op == 0xA:
            mask = 1 << (imm & 7)
            src = (imm >> 5) & 3
            val = [(imm >> 7) & 1, self.carry, rd_val >> 7, rd_val & 1][src]
            if src:
                val ^= (imm >> 7) & 1
            def upd(cur, width):
                return ((cur | mask) if val else (cur & ~mask)) & ((1 << width) - 1)
            if sub == 0: o.uio_data = upd(o.uio_data, 8)
            elif sub == 1: o.uio_oe = upd(o.uio_oe, 8)
            elif sub == 2: o.uo_data = upd(o.uo_data, 7)
            else: o.uo_oe = upd(o.uo_oe, 7)
            if src == 2: self.regs[rd] = ((rd_val << 1) & 0xFF) | (rd_val >> 7); self.carry = rd_val >> 7
            if src == 3: self.regs[rd] = (rd_val >> 1) | ((rd_val & 1) << 7); self.carry = rd_val & 1
            self.pc = nxt
        elif op == 0xB:
            if sub == 0: self.regs[rd] = (rd_val << 1) & 0xFF; self.carry = rd_val >> 7
            elif sub == 1: self.regs[rd] = rd_val >> 1; self.carry = rd_val & 1
            elif sub == 2: self.regs[rd] = (pin_now << 7) | (rd_val >> 1); self.carry = rd_val & 1
            else: self.regs[rd] = ((rd_val << 1) & 0xFF) | pin_now; self.carry = rd_val >> 7
            self.pc = nxt
        elif op == 0xC:
            fn = imm & 7
            if fn == 0: self.regs[rd] = rs_val
            elif fn == 1:
                s = rd_val + rs_val; self.regs[rd] = s & 0xFF; self.carry = s >> 8
            elif fn == 2:
                s = rd_val - rs_val; self.regs[rd] = s & 0xFF; self.carry = 1 if s < 0 else 0
            elif fn == 3: self.regs[rd] = rd_val & rs_val
            elif fn == 4: self.regs[rd] = rd_val | rs_val
            elif fn == 5: self.regs[rd] = rd_val ^ rs_val
            elif fn == 6: self.regs[rd] = self.carry
            else: self.regs[rd] = (~rd_val) & 0xFF
            self.pc = nxt
        elif op == 0xD:
            if sub == 0: self.trace_we = True; self.trace_data = imm
            elif sub == 1: self.trace_we = True; self.trace_data = rd_val
            elif sub == 2: self.mbox_out = rd_val; self.mbox_out_we = True
            else: self.done = True
            self.pc = nxt
        elif op == 0xE:
            self.running = False; o.uio_oe = 0; o.uo_oe = 0
        else:
            self.running = False; o.uio_oe = 0; o.uo_oe = 0; self.fault_illegal = True


@dataclass
class Command:
    """A host frame as it takes effect in the core clock domain."""
    word: int

    @property
    def cmd(self) -> int:
        return (self.word >> 28) & 0xF

    def bit(self, i: int) -> int:
        return (self.word >> i) & 1

    def bits(self, hi: int, lo: int) -> int:
        return (self.word >> lo) & ((1 << (hi - lo + 1)) - 1)


# Host frame builders -------------------------------------------------------
def cmd_gpio(uio_oe: int, uio_data: int, uo_data: int = 0) -> int:
    return (0x1 << 28) | ((uo_data & 0x7F) << 16) | ((uio_data & 0xFF) << 8) | (uio_oe & 0xFF)


def cmd_program(engine: int, addr: int, word: int) -> int:
    return (0x2 << 28) | (engine << 27) | (addr << 19) | (word << 3)


def cmd_run(start0=False, stop0=False, start1=False, stop1=False, clear_faults=False,
            ts_reset=False, arm=False, disarm=False, ack_rx0=False, ack_rx1=False,
            clear_done=False, start_pc=0) -> int:
    flags = [start0, stop0, start1, stop1, clear_faults, ts_reset, arm, disarm,
             ack_rx0, ack_rx1, clear_done]
    v = sum(int(bool(f)) << i for i, f in enumerate(flags))
    return (0x3 << 28) | ((start_pc & 0xFF) << 16) | v


def cmd_traceptr(index: int) -> int:
    return (0x4 << 28) | (index & (TRACE_DEPTH - 1))


def cmd_perm(engine: int, uio_mask: int, uo_mask: int) -> int:
    return (0x5 << 28) | (engine << 27) | ((uo_mask & 0x7F) << 8) | (uio_mask & 0xFF)


def cmd_mbox(engine: int, data: int) -> int:
    return (0x6 << 28) | (engine << 27) | (data & 0xFF)


def cmd_capture(watch: int, trig_src: int, pin_capture: bool, stop_on_full: bool = True) -> int:
    return ((0x7 << 28) | (int(stop_on_full) << 17) | (int(pin_capture) << 16)
            | ((trig_src & 7) << 13) | (watch & 0x1FFF))


def cmd_readsel(sel: int) -> int:
    return (0x8 << 28) | (sel & 7)


READ_STATUS, READ_MBOX, READ_TRACE, READ_TIME, READ_ID = 0, 1, 2, 3, 4


class Chip:
    """Top-level model.  Call step() once per rising clock edge."""

    def __init__(self) -> None:
        self.eng = [Engine(), Engine()]
        self.imem = [[0] * PROG_DEPTH, [0] * PROG_DEPTH]
        self.trace = [0] * TRACE_DEPTH
        self.pins_s1 = self.pins_q = self.pins_qq = 0
        self.timestamp = 0
        self.host_uio_data = self.host_uio_oe = 0
        self.host_uo_data = 0
        self.perm_uio = [0xFF, 0xFF]
        self.perm_uo = [0x7F, 0x7F]
        self.mbox_tx = [0, 0]
        self.mbox_tx_valid = [False, False]
        self.mbox_rx = [0, 0]
        self.mbox_rx_pending = [False, False]
        self.fault_collision = self.fault_perm = False
        self.watch_mask = 0
        self.trig_src = 0
        self.pin_capture_en = False
        self.stop_on_full = True
        self.armed = self.triggered = self.trace_overflow = False
        self.trace_wptr = 0
        self.trace_rptr = 0
        self.trace_count = 0
        self.read_sel = 0
        # registered command side effects (take effect one edge later)
        self._prog_we = [False, False]
        self._prog_waddr = 0
        self._prog_wdata = 0
        self._start = [False, False]
        self._stop = [False, False]
        self._clear_done = False
        self.start_pc = 0

    # -- combinational views --------------------------------------------
    def _arbitrate(self):
        e0, e1 = self.eng[0].out, self.eng[1].out
        req0, req1 = e0.uio_oe & self.perm_uio[0], e1.uio_oe & self.perm_uio[1]
        ro0, ro1 = e0.uo_oe & self.perm_uo[0], e1.uo_oe & self.perm_uo[1]
        coll, collo = req0 & req1, ro0 & ro1
        d0, d1 = req0 & ~coll, req1 & ~coll
        o0, o1 = ro0 & ~collo, ro1 & ~collo
        free = (~(req0 | req1)) & 0xFF
        freeo = (~(ro0 | ro1)) & 0x7F
        uio_out = (d0 & e0.uio_data) | (d1 & e1.uio_data) | (free & self.host_uio_data)
        uio_oe = d0 | d1 | (free & self.host_uio_oe)
        uo = (o0 & e0.uo_data) | (o1 & e1.uo_data) | (freeo & self.host_uo_data)
        violation = ((e0.uio_oe & ~self.perm_uio[0]) | (e1.uio_oe & ~self.perm_uio[1]) |
                     (e0.uo_oe & ~self.perm_uo[0]) | (e1.uo_oe & ~self.perm_uo[1])) != 0
        return uio_out, uio_oe, uo, (coll | collo) != 0, violation

    @property
    def uio_out(self) -> int:
        return self._arbitrate()[0]

    @property
    def uio_oe(self) -> int:
        return self._arbitrate()[1]

    @property
    def uo_out(self) -> int:
        """uo_out[7:1]; bit 0 (MISO) is owned by the serial link."""
        return self._arbitrate()[2] << 1

    def status_word(self) -> int:
        e0, e1 = self.eng
        bits = [e0.running, e1.running, e0.done, e1.done, self.fault_collision, self.fault_perm,
                e0.fault_illegal, e1.fault_illegal, self.mbox_tx_valid[0], self.mbox_tx_valid[1],
                self.mbox_rx_pending[0], self.mbox_rx_pending[1], self.armed, self.triggered,
                self.trace_overflow, self.trace_count >= TRACE_DEPTH]
        v = sum(int(bool(b)) << i for i, b in enumerate(bits))
        return v | (self.trace_count << 16) | (VERSION << 24)

    def read_register(self, sel: int) -> int:
        if sel == READ_STATUS:
            return self.status_word()
        if sel == READ_MBOX:
            return (self.eng[1].pc << 24) | (self.eng[0].pc << 16) | (self.mbox_rx[1] << 8) | self.mbox_rx[0]
        if sel == READ_TRACE:
            return self.trace[self.trace_rptr]
        if sel == READ_TIME:
            return (self.trace_wptr << 16) | self.timestamp
        return 0x50494F31

    # -- one clock edge ----------------------------------------------------
    def step(self, uio_in: int, ui_in: int, command: Command | None = None) -> None:
        """Advance one clock.  `uio_in`/`ui_in` are the pad values sampled at this
        edge; `command` is a frame whose cfg_request is high during this cycle."""
        pins_raw = (((ui_in >> 7) & 1) << 12) | (((ui_in >> 3) & 0xF) << 8) | (uio_in & 0xFF)
        e = self.eng
        # values used by the engines this cycle (pre-edge state)
        pin_in = [((e[1 - g].running) << 15) | (self.mbox_rx_pending[g] << 14)
                  | (self.mbox_tx_valid[g] << 13) | self.pins_q for g in range(2)]
        pin_prev = [(pin_in[g] & 0xE000) | self.pins_qq for g in range(2)]
        _, _, _, collision, violation = self._arbitrate()
        pin_change = (self.pins_q ^ self.pins_qq) & self.watch_mask
        trig_rise = (self.pins_q >> 12) & 1 and not (self.pins_qq >> 12) & 1
        trig_fall = (not (self.pins_q >> 12) & 1) and (self.pins_qq >> 12) & 1

        # Engine pulses (trace, mailbox) are registered in the RTL, so the top
        # level reacts to the pulses produced by the *previous* edge.
        pulses = [(x.trace_we, x.trace_data, x.mbox_in_take, x.mbox_out_we, x.mbox_out) for x in e]
        for g in range(2):
            e[g].step(self.imem[g][e[g].pc % PROG_DEPTH], pin_in[g], pin_prev[g], self.mbox_tx[g],
                      self._start[g], self.start_pc, self._stop[g], self._clear_done)
        # program memory writes registered last cycle
        for g in range(2):
            if self._prog_we[g]:
                self.imem[g][self._prog_waddr % PROG_DEPTH] = self._prog_wdata

        # trigger / capture
        ev0, ev1 = pulses[0][0], pulses[1][0]
        trig_hit = [True, trig_rise, trig_fall, ev0, ev1, pin_change != 0, False, False][self.trig_src]
        capturing = self.triggered or (self.armed and trig_hit)
        want_pin = capturing and self.pin_capture_en and pin_change != 0
        full = self.trace_count >= TRACE_DEPTH
        block = full and self.stop_on_full
        want = ev0 or ev1 or want_pin
        we = want and not block
        drop = (want and block) or (ev0 and (ev1 or want_pin)) or (ev1 and want_pin)
        if ev0:
            wdata = (self.timestamp << 16) | pulses[0][1]
        elif ev1:
            wdata = (self.timestamp << 16) | (1 << 13) | pulses[1][1]
        else:
            wdata = (self.timestamp << 16) | (1 << 14) | self.pins_q

        # sequential updates of top-level state
        self.pins_s1, self.pins_q, self.pins_qq = pins_raw, self.pins_s1, self.pins_q
        self.timestamp = (self.timestamp + 1) & 0xFFFF
        if collision:
            self.fault_collision = True
        if violation:
            self.fault_perm = True
        if self.armed and trig_hit:
            self.armed, self.triggered = False, True
        if we:
            self.trace[self.trace_wptr] = wdata
            if self.trace_wptr == TRACE_DEPTH - 1:
                self.trace_overflow = True
            self.trace_wptr = (self.trace_wptr + 1) % TRACE_DEPTH
            if not full:
                self.trace_count += 1
        if drop:
            self.trace_overflow = True
        for g in range(2):
            if pulses[g][2]:
                self.mbox_tx_valid[g] = False
            if pulses[g][3]:
                self.mbox_rx[g] = pulses[g][4]
                self.mbox_rx_pending[g] = True
        self._prog_we = [False, False]
        self._start = [False, False]
        self._stop = [False, False]
        self._clear_done = False

        if command is not None:
            self._apply(command, e)

    def _apply(self, c: Command, e) -> None:
        k = c.cmd
        if k == 0x1:
            self.host_uio_oe = c.bits(7, 0)
            self.host_uio_data = c.bits(15, 8)
            self.host_uo_data = c.bits(22, 16)
        elif k == 0x2:
            self._prog_waddr = c.bits(26, 19)
            self._prog_wdata = c.bits(18, 3)
            g = c.bit(27)
            if not e[g].running:
                self._prog_we[g] = True
        elif k == 0x3:
            self.start_pc = c.bits(23, 16)
            self._start = [bool(c.bit(0)), bool(c.bit(2))]
            self._stop = [bool(c.bit(1)), bool(c.bit(3))]
            if c.bit(4):
                self.fault_collision = self.fault_perm = False
            if c.bit(5):
                self.timestamp = 0
            if c.bit(6):
                self.armed, self.triggered, self.trace_overflow = True, False, False
                self.trace_wptr, self.trace_count = 0, 0
            if c.bit(7):
                self.armed = self.triggered = False
            if c.bit(8):
                self.mbox_rx_pending[0] = False
            if c.bit(9):
                self.mbox_rx_pending[1] = False
            self._clear_done = bool(c.bit(10))
        elif k == 0x4:
            self.trace_rptr = c.word & (TRACE_DEPTH - 1)
        elif k == 0x5:
            g = c.bit(27)
            self.perm_uio[g] = c.bits(7, 0)
            self.perm_uo[g] = c.bits(14, 8)
        elif k == 0x6:
            g = c.bit(27)
            self.mbox_tx[g] = c.bits(7, 0)
            self.mbox_tx_valid[g] = True
        elif k == 0x7:
            self.watch_mask = c.bits(12, 0)
            self.trig_src = c.bits(15, 13)
            self.pin_capture_en = bool(c.bit(16))
            self.stop_on_full = bool(c.bit(17))
        elif k == 0x8:
            self.read_sel = c.bits(2, 0)
