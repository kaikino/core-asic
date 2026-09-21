"""MicroPython host driver for the Tiny Tapeout demo board (RP2040).

Runs on the board's MicroPython REPL after `tt.shuttle.<project>.enable()`:
bit-bangs the chip's SPI-mode-0 host link on ui_in[2:0] / uo_out[0], loads a
program assembled with tools/proto_asm.py (`--frames`), and reads registers.

    from tt_board_host import ProtoHost
    h = ProtoHost(tt)                      # tt = the demo board object
    h.reset()
    h.load(0, FRAMES)                      # list of 32-bit PROGRAM frames
    h.run(start0=True)
    print(hex(h.read(0)))                  # STATUS

Frame encodings match tools/proto_ref.py (cmd_* builders); the constants
below mirror them so this file has no dependencies.
"""
import time


class ProtoHost:
    SCK, MOSI, CS_N = 0, 1, 2   # ui_in bits
    MISO = 0                     # uo_out bit

    def __init__(self, tt, clock_hz=40_000_000):
        self.tt = tt
        self.tt.clock_project_PWM(clock_hz)
        self._ui = 0b100            # CS_N high, SCK low
        self._write_ui()

    # -- pins --------------------------------------------------------------
    def _write_ui(self):
        self.tt.ui_in.value = self._ui

    def _set(self, bit, value):
        self._ui = (self._ui | (1 << bit)) if value else (self._ui & ~(1 << bit))
        self._write_ui()

    def _miso(self):
        return (self.tt.uo_out.value >> self.MISO) & 1

    def reset(self):
        self.tt.reset_project(True)
        time.sleep_ms(1)
        self.tt.reset_project(False)

    # -- link --------------------------------------------------------------
    def xfer(self, word):
        """Send one 32-bit frame, return the 32-bit response."""
        rx = 0
        self._set(self.CS_N, 0)
        for i in range(31, -1, -1):
            self._set(self.MOSI, (word >> i) & 1)
            rx = (rx << 1) | self._miso()
            self._set(self.SCK, 1)
            self._set(self.SCK, 0)
        self._set(self.CS_N, 1)
        return rx

    def read(self, sel):
        self.xfer((0x8 << 28) | (sel & 7))
        return self.xfer(0)

    # -- commands ----------------------------------------------------------
    def load(self, engine, frames_or_words, base=0):
        for i, w in enumerate(frames_or_words):
            frame = w if w >> 28 == 0x2 else (0x2 << 28) | (engine << 27) | ((base + i) << 19) | (w << 3)
            self.xfer(frame)

    def run(self, start0=False, stop0=False, start1=False, stop1=False, clear_faults=False,
            ts_reset=False, arm=False, disarm=False, ack_rx0=False, ack_rx1=False,
            clear_done=False, start_pc=0):
        flags = [start0, stop0, start1, stop1, clear_faults, ts_reset, arm, disarm, ack_rx0, ack_rx1, clear_done]
        v = 0
        for i, f in enumerate(flags):
            v |= int(bool(f)) << i
        return self.xfer((0x3 << 28) | ((start_pc & 0xFF) << 16) | v)

    def gpio(self, uio_oe, uio_data, uo_data=0):
        return self.xfer((0x1 << 28) | ((uo_data & 0x7F) << 16) | ((uio_data & 0xFF) << 8) | (uio_oe & 0xFF))

    def mbox(self, engine, data):
        return self.xfer((0x6 << 28) | (engine << 27) | (data & 0xFF))

    def perm(self, engine, uio_mask, uo_mask):
        return self.xfer((0x5 << 28) | (engine << 27) | ((uo_mask & 0x7F) << 8) | (uio_mask & 0xFF))

    def fifo_reset(self, fcs_mode=False):
        return self.xfer((0x9 << 28) | (int(fcs_mode) << 10) | (1 << 9))

    def fifo_push(self, data, fcs_mode=False):
        return self.xfer((0x9 << 28) | (int(fcs_mode) << 10) | (1 << 8) | (data & 0xFF))

    def capture(self, watch, trig_src, pin_capture, stop_on_full=True):
        return self.xfer((0x7 << 28) | (int(stop_on_full) << 17) | (int(pin_capture) << 16)
                         | ((trig_src & 7) << 13) | (watch & 0x1FFF))

    def tdc(self, channel, source, trace=False):
        return self.xfer((0xA << 28) | (channel << 27) | (int(trace) << 8) | (source & 0xF))

    def dtc(self, channel, pin, enable, tap=0):
        return self.xfer((0xA << 28) | (channel << 27) | (1 << 26) | ((tap & 0xFF) << 16) | (int(enable) << 8) | (pin & 7))

    def crc_config(self, poly, init, xorout):
        for which, value in enumerate((poly, init, xorout)):
            for lane in range(4):
                self.xfer((0xB << 28) | (which << 26) | (lane << 24) | ((value >> (8 * lane)) & 0xFF))

    def trace(self, count):
        out = []
        for i in range(count):
            self.xfer((0x4 << 28) | (i & 31))
            out.append(self.read(2))
        return out

    def calibrate(self):
        """Stages per half period (12.5 ns) on this die: the delay-line unit."""
        self.tdc(0, 13)
        time.sleep_us(10)
        return self.read(5) & 0xFF
