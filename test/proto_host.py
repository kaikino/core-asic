"""cocotb host-side driver for the protocol emulator's control link.

The driver bit-bangs SPI mode 0 on ui_in[2:0] with SCK_HALF core clocks per
half period and keeps a cycle-accurate `Chip` reference model in lock-step
with the RTL: `Harness.tick()` advances both by one clock and compares every
target pin (uio_out, uio_oe, uo_out[7:1]).
"""
from __future__ import annotations

import os
import sys

from cocotb.triggers import ClockCycles, NextTimeStep, ReadOnly, RisingEdge

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from proto_ref import (  # noqa: E402
    READ_STATUS, Chip, Command, cmd_program, cmd_readsel, cmd_run)

SCK_HALF = 5  # core clocks per SCK half period (4 MHz at 40 MHz)


class Harness:
    def __init__(self, dut, compare: bool = True):
        self.dut = dut
        self.model = Chip()
        self.compare = compare
        self.pending: list[int] = []
        self.uio_in = 0
        self.ui_target = 0  # ui_in[7:3]: TARGET_IN0..3 and TRIGGER_IN
        self.cycle = 0
        self.sck = 0
        self.mosi = 0
        self.cs_n = 1
        self.after_tick = None  # optional callback(harness) run after each edge
        self._scheduled = None  # command the RTL will apply at the next edge

    # -- pads ------------------------------------------------------------
    @property
    def ui_in(self) -> int:
        return (self.ui_target << 3) | (self.cs_n << 2) | (self.mosi << 1) | self.sck

    def drive(self) -> None:
        self.dut.ui_in.value = self.ui_in
        self.dut.uio_in.value = self.uio_in

    @property
    def uio_out(self) -> int:
        return int(self.dut.uio_out.value)

    @property
    def uio_oe(self) -> int:
        return int(self.dut.uio_oe.value)

    @property
    def uo_out(self) -> int:
        return int(self.dut.uo_out.value)

    async def reset(self) -> None:
        self.dut.ena.value = 1
        self.dut.rst_n.value = 0
        self.drive()
        await ClockCycles(self.dut.clk, 5)
        await RisingEdge(self.dut.clk)
        self.dut.rst_n.value = 1

    async def tick(self, n: int = 1) -> None:
        """Advance n clocks; after each edge step the model and compare pins."""
        for _ in range(n):
            self.drive()  # pads carry exactly what the model is told
            uio_in, ui_in = self.uio_in, self.ui_in
            await RisingEdge(self.dut.clk)
            await ReadOnly()
            # cfg_request seen after edge E means the RTL applies it at E+1,
            # so the model consumes it on the following step.
            cmd, self._scheduled = self._scheduled, None
            self.model.step(uio_in, ui_in, cmd)
            if int(self.dut.user_project.cfg_request.value):
                self._scheduled = Command(self.pending.pop(0))
            self.cycle += 1
            if self.compare:
                self.check()
            await NextTimeStep()
            if self.after_tick:
                self.after_tick(self)

    def check(self) -> None:
        got = (self.uio_out, self.uio_oe, self.uo_out & 0xFE)
        exp = (self.model.uio_out, self.model.uio_oe, self.model.uo_out)
        ts = int(self.dut.user_project.timestamp.value)
        if ts != self.model.timestamp:
            raise AssertionError(f"cycle {self.cycle}: rtl timestamp {ts} model {self.model.timestamp}")
        if got != exp:
            e0, e1 = self.model.eng
            up = self.dut.user_project
            rtl = (f"rtl pc0={int(up.pc[0].value):02x} pc1={int(up.pc[1].value):02x} "
                   f"e0.data={int(up.e_uio_data[0].value):02x} e0.oe={int(up.e_uio_oe[0].value):02x}")
            raise AssertionError(
                f"cycle {self.cycle}: rtl uio_out/oe/uo={got[0]:02x}/{got[1]:02x}/{got[2]:02x} "
                f"model={exp[0]:02x}/{exp[1]:02x}/{exp[2]:02x} model pc0={e0.pc:02x} pc1={e1.pc:02x} "
                f"model e0.data={e0.out.uio_data:02x} e0.oe={e0.out.uio_oe:02x} | {rtl}")

    # -- link ------------------------------------------------------------
    async def xfer(self, word: int) -> int:
        """Send one 32-bit frame and return the 32-bit MISO response."""
        self.pending.append(word)
        rx = 0
        self.cs_n = 0
        await self.tick(SCK_HALF)
        for i in range(31, -1, -1):
            self.mosi = (word >> i) & 1
            await self.tick(SCK_HALF)
            rx = (rx << 1) | (self.uo_out & 1)
            self.sck = 1
            await self.tick(SCK_HALF)
            self.sck = 0
        await self.tick(SCK_HALF)
        self.cs_n = 1
        await self.tick(SCK_HALF)
        return rx

    async def read(self, sel: int) -> int:
        await self.xfer(cmd_readsel(sel))
        return await self.xfer(0)

    async def load(self, engine: int, words: list[int], base: int = 0) -> None:
        for i, w in enumerate(words):
            await self.xfer(cmd_program(engine, base + i, w))

    async def start(self, engine: int, pc: int = 0) -> None:
        await self.xfer(cmd_run(start0=engine == 0, start1=engine == 1, start_pc=pc))

    async def stop(self, engine: int) -> None:
        await self.xfer(cmd_run(stop0=engine == 0, stop1=engine == 1))

    async def wait_idle(self, engine: int, limit: int = 200) -> int:
        for _ in range(limit):
            status = await self.read(READ_STATUS)
            if not (status >> engine) & 1:
                return status
        raise AssertionError(f"engine {engine} still running")
