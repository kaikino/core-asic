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
    """Return bytes following the first SFD in an MSB-first bit list."""
    pattern = [(sfd >> (7 - i)) & 1 for i in range(8)]
    for i in range(len(bits) - 8):
        if bits[i:i + 8] == pattern:
            rest = bits[i + 8:]
            return [sum(b << (7 - j) for j, b in enumerate(rest[k:k + 8]))
                    for k in range(0, len(rest) - 7, 8)]
    return []
