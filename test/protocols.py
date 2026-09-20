"""Pure-Python protocol monitors and peers used by the cocotb suite.

Every object is fed one sample per core clock so decoded timing is exact.
"""
from __future__ import annotations


class UartMonitor:
    """Decodes 8N1 frames from a per-clock line sample with a known bit period."""

    def __init__(self, period: int):
        self.period = period
        self.bytes: list[int] = []
        self.errors: list[str] = []
        self.prev = 1
        self.t = None  # clocks since start-bit edge
        self.samples: list[int] = []

    def sample(self, line: int) -> None:
        if self.t is None:
            if self.prev == 1 and line == 0:
                self.t = 0
                self.samples = []
        else:
            self.t += 1
            # sample bits at 1.5P .. 9.5P after the start edge
            if self.t % self.period == self.period // 2 and self.t // self.period >= 1:
                self.samples.append(line)
                if len(self.samples) == 9:
                    if self.samples[8] != 1:
                        self.errors.append("framing error")
                    self.bytes.append(sum(b << i for i, b in enumerate(self.samples[:8])))
                    self.t = None
        self.prev = line


class UartSource:
    """Generates an 8N1 line waveform sample-by-sample."""

    def __init__(self, period: int, idle_gap: int = 20):
        self.period = period
        self.queue: list[int] = []
        self.gap = idle_gap
        self.stream: list[int] = []

    def send(self, byte: int) -> None:
        bits = [0] + [(byte >> i) & 1 for i in range(8)] + [1]
        for b in bits:
            self.stream += [b] * self.period
        self.stream += [1] * self.gap

    def next(self) -> int:
        return self.stream.pop(0) if self.stream else 1


class SpiSlave:
    """Mode-0 slave: samples MOSI on SCK rise, updates MISO on SCK fall."""

    def __init__(self, reply: list[int]):
        self.reply = list(reply)
        self.received: list[int] = []
        self.sck = 0
        self.cs_n = 1
        self.shift_in = 0
        self.nbits = 0
        self.tx = self.reply.pop(0) if self.reply else 0
        self.txbit = 0
        self.miso = (self.tx >> 7) & 1

    def step(self, sck: int, mosi: int, cs_n: int) -> int:
        if cs_n and not self.cs_n:
            pass
        if not cs_n and self.cs_n:  # falling CS: reload
            self.shift_in, self.nbits, self.txbit = 0, 0, 0
            self.miso = (self.tx >> 7) & 1
        if not cs_n:
            if sck and not self.sck:  # rising edge
                self.shift_in = ((self.shift_in << 1) | mosi) & 0xFF
                self.nbits += 1
                if self.nbits == 8:
                    self.received.append(self.shift_in)
                    self.nbits = 0
                    self.tx = self.reply.pop(0) if self.reply else 0
            if not sck and self.sck:  # falling edge
                self.txbit = (self.txbit + 1) % 8
                if self.txbit == 0:
                    self.miso = (self.tx >> 7) & 1
                else:
                    self.miso = (self.tx >> (7 - self.txbit)) & 1
        self.sck, self.cs_n = sck, cs_n
        return self.miso


class I2cSlave:
    """Open-drain I2C slave that ACKs one address; records the transaction."""

    def __init__(self, address: int):
        self.address = address
        self.events: list[str] = []
        self.bytes: list[int] = []
        self.scl = 1
        self.sda = 1
        self.bits: list[int] = []
        self.active = False
        self.pull_sda = 0
        self.ack_pending = False
        self.byte_index = 0

    def step(self, scl: int, sda: int) -> int:
        """Return 1 while the slave pulls SDA low."""
        if self.scl and scl:
            if self.sda and not sda:
                self.events.append("START"); self.active = True; self.bits = []; self.byte_index = 0
            elif not self.sda and sda:
                self.events.append("STOP"); self.active = False
        if scl and not self.scl and self.active:  # rising SCL: sample bit
            if self.ack_pending:
                self.ack_pending = False
            else:
                self.bits.append(sda)
        if not scl and self.scl and self.active:  # falling SCL
            if len(self.bits) == 8:
                b = sum(bit << (7 - i) for i, bit in enumerate(self.bits))
                self.bits = []
                self.bytes.append(b)
                ok = (b >> 1) == self.address if self.byte_index == 0 else True
                self.byte_index += 1
                self.pull_sda = 1 if ok else 0
                self.ack_pending = True
            else:
                self.pull_sda = 0
        self.scl, self.sda = scl, sda
        return self.pull_sda


def manchester_decode(samples: list[int]) -> list[int]:
    """Decode a 4-samples-per-bit Manchester stream into bits (802.3 polarity)."""
    bits = []
    for i in range(0, len(samples) - 3, 4):
        first, second = samples[i], samples[i + 2]
        if first != second:
            bits.append(second)
    return bits


def find_payload(bits: list[int], sfd: int = 0xD5) -> list[int]:
    """Return the bytes following the first SFD in an LSB-first bit stream."""
    pattern = [(sfd >> i) & 1 for i in range(8)]  # 10101011 on the wire
    for i in range(len(bits) - 8):
        if bits[i:i + 8] == pattern:
            rest = bits[i + 8:]
            return [sum(b << j for j, b in enumerate(rest[k:k + 8]))
                    for k in range(0, len(rest) - 7, 8)]
    return []


def frame_ok(frame: list[int]) -> bool:
    """True if the last four bytes are the CRC-32 FCS of the preceding bytes."""
    import zlib
    if len(frame) < 5:
        return False
    fcs = zlib.crc32(bytes(frame[:-4])) & 0xFFFFFFFF
    return frame[-4:] == [(fcs >> (8 * k)) & 0xFF for k in range(4)]


class JtagTap:
    """A minimal TAP: after Test-Logic-Reset the DR path holds IDCODE."""

    STATES = {
        "RESET": ("IDLE", "RESET"), "IDLE": ("IDLE", "SELDR"),
        "SELDR": ("CAPDR", "SELIR"), "CAPDR": ("SHDR", "EX1DR"), "SHDR": ("SHDR", "EX1DR"),
        "EX1DR": ("PAUDR", "UPDR"), "PAUDR": ("PAUDR", "EX2DR"), "EX2DR": ("SHDR", "UPDR"),
        "UPDR": ("IDLE", "SELDR"),
        "SELIR": ("CAPIR", "RESET"), "CAPIR": ("SHIR", "EX1IR"), "SHIR": ("SHIR", "EX1IR"),
        "EX1IR": ("PAUIR", "UPIR"), "PAUIR": ("PAUIR", "EX2IR"), "EX2IR": ("SHIR", "UPIR"),
        "UPIR": ("IDLE", "SELDR"),
    }

    def __init__(self, idcode: int):
        self.idcode = idcode
        self.state = "RESET"
        self.dr = 0
        self.tck = 0
        self.tdo = 0
        self.visited: list[str] = []

    def step(self, tck: int, tms: int, tdi: int) -> int:
        if tck and not self.tck:                      # rising edge: state + shift
            if self.state == "SHDR":
                self.dr = (self.dr >> 1) | (tdi << 31)
            self.state = self.STATES[self.state][tms]
            self.visited.append(self.state)
            if self.state == "CAPDR":
                self.dr = self.idcode
        if not tck and self.tck:                      # falling edge: TDO
            self.tdo = self.dr & 1
        self.tck = tck
        return self.tdo


class SwdTarget:
    """SWD target that answers a DPIDR read (request 0xA5) with ACK OK and an ID.

    States: wait_reset (count SWDIO=1 clocks; 50 is a line reset), reset (SWDIO
    still high), idle_low (host sent idle zeros), req (collecting 8 bits from
    the start bit), turn (turnaround clock), reply (driving ACK, data, parity).
    A malformed request (the switch sequence looks like one) returns to
    wait_reset, which is what the host's second line reset resolves."""

    def __init__(self, dpidr: int):
        self.dpidr = dpidr
        self.clk = 0
        self.state = "wait_reset"
        self.ones = 0
        self.req = 0
        self.req_bits = 0
        self.reply: list[int] = []
        self.driving = False
        self.out = 1
        self.requests: list[int] = []

    def step(self, clk: int, swdio: int) -> tuple[bool, int]:
        """Return (driving, value) for the target's side of SWDIO."""
        if clk and not self.clk:                      # rising edge: sample the host
            st = self.state
            if st == "wait_reset":
                self.ones = self.ones + 1 if swdio else 0
                if self.ones >= 50:
                    self.state = "reset"
            elif st == "reset":
                if not swdio:
                    self.state = "idle_low"
            elif st == "idle_low":
                if swdio:
                    self.req, self.req_bits, self.state = 1, 1, "req"
            elif st == "req":
                self.req |= swdio << self.req_bits
                self.req_bits += 1
                if self.req_bits == 8:
                    r = self.req
                    parity_ok = (bin(r & 0x1E).count("1") & 1) == ((r >> 5) & 1)
                    if (r >> 7) & 1 and not (r >> 6) & 1 and parity_ok:
                        self.requests.append(r)
                        if r == 0xA5:
                            par = bin(self.dpidr).count("1") & 1
                            self.reply = [1, 0, 0] + [(self.dpidr >> i) & 1 for i in range(32)] + [par]
                            self.state = "turn"
                        else:
                            self.state = "idle_low"
                    else:
                        self.ones, self.state = 0, "wait_reset"
            elif st == "turn":
                self.state = "reply"
            elif st == "reply" and not self.reply:
                self.driving, self.ones, self.state = False, 0, "wait_reset"
        if not clk and self.clk:                      # falling edge: drive the next bit
            if self.state == "reply" and self.reply:
                self.driving, self.out = True, self.reply.pop(0)
            elif self.state == "reply":
                self.driving = False
        self.clk = clk
        return self.driving, self.out


class Ps2Host:
    """Samples DATA on CLK falling edges and decodes 11-bit device frames."""

    def __init__(self):
        self.clk = 1
        self.bits: list[int] = []
        self.bytes: list[int] = []
        self.errors: list[str] = []

    def step(self, clk: int, data: int) -> None:
        if not clk and self.clk:
            self.bits.append(data)
            if len(self.bits) == 11:
                start, payload, parity, stop = self.bits[0], self.bits[1:9], self.bits[9], self.bits[10]
                value = sum(b << i for i, b in enumerate(payload))
                if start != 0 or stop != 1:
                    self.errors.append("framing")
                if (sum(payload) + parity) % 2 != 1:
                    self.errors.append("parity")
                self.bytes.append(value)
                self.bits = []
        self.clk = clk
