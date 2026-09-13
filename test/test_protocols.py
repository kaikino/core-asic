# SPDX-License-Identifier: Apache-2.0
"""Directed protocol tests: UART, SPI and I2C microprograms decoded by independent monitors.

Every test keeps the Python reference model (tools/proto_ref.py) in lock-step
with the RTL and compares uio_out, uio_oe, uo_out[7:1] and the timestamp on
every clock.
"""
import os
import random

import cocotb

from common import example, read_trace, setup  # noqa: F401
from proto_asm import assemble_program  # noqa: F401
from proto_ref import (  # noqa: F401
    READ_ID, READ_MBOX, READ_STATUS, READ_TIME, READ_TRACE, TRACE_DEPTH,
    cmd_capture, cmd_gpio, cmd_mbox, cmd_perm, cmd_run, cmd_traceptr)
from protocols import (  # noqa: F401
    I2cSlave, SpiSlave, UartMonitor, UartSource, find_payload, manchester_decode)


@cocotb.test()
async def test_uart_tx(dut):
    """Milestone 3/5: loaded UART transmitter decoded by an independent monitor."""
    h = await setup(dut)
    period = 20
    mon = UartMonitor(period)
    # An undriven line idles high through its pull-up.
    h.after_tick = lambda hh: mon.sample((hh.uio_out & 1) if (hh.uio_oe & 1) else 1)
    await h.load(0, example("uart_tx.pio", BIT=period - 3))
    await h.start(0)
    payload = [0x55, 0x00, 0xFF, 0xA3, 0x7E]
    for b in payload:
        await h.xfer(cmd_mbox(0, b))
        await h.tick(11 * period)
    assert mon.bytes == payload, f"decoded {mon.bytes}"
    assert not mon.errors


@cocotb.test()
async def test_uart_rx(dut):
    """UART receiver: bytes reach the mailbox and the trace buffer."""
    h = await setup(dut)
    period = 20
    src = UartSource(period)
    for b in (0x31, 0xC5, 0x80, 0x01):
        src.send(b)

    def feed(hh):
        hh.ui_target = (hh.ui_target & ~1) | src.next()
    h.ui_target = 1
    await h.load(0, example("uart_rx.pio", BIT=period - 3, HALF=period // 2 - 1))
    await h.xfer(cmd_run(arm=True))
    await h.start(0)
    h.after_tick = feed
    await h.tick(4 * 11 * period + 200)
    status = await h.read(READ_STATUS)
    assert (status >> 16) & 0x3F == 4, f"expected 4 trace entries, status={status:08x}"
    entries = await read_trace(h, 4)
    assert [e & 0xFF for e in entries] == [0x31, 0xC5, 0x80, 0x01]
    assert (await h.read(READ_MBOX)) & 0xFF == 0x01


@cocotb.test()
async def test_spi_master(dut):
    """SPI mode-0 master against a Python slave; both directions checked."""
    h = await setup(dut)
    slave = SpiSlave(reply=[0x9C, 0x3B, 0xF0])

    def peer(hh):
        miso = slave.step((hh.uio_out >> 1) & 1, hh.uio_out & 1, (hh.uio_out >> 2) & 1)
        hh.uio_in = (hh.uio_in & ~0x08) | (miso << 3)
    h.after_tick = peer
    await h.load(0, example("spi_master.pio"))
    await h.xfer(cmd_run(arm=True))
    await h.start(0)
    for b in (0xA5, 0x12, 0xFF):
        await h.xfer(cmd_mbox(0, b))
        await h.tick(250)
    assert slave.received == [0xA5, 0x12, 0xFF], slave.received
    entries = await read_trace(h, 3)
    assert [e & 0xFF for e in entries] == [0x9C, 0x3B, 0xF0]


@cocotb.test()
async def test_i2c_master(dut):
    """Open-drain I2C write transaction with an ACKing Python slave."""
    h = await setup(dut)
    slave = I2cSlave(address=0x50)

    def bus(hh):
        oe = hh.uio_oe
        scl = 0 if oe & 0x02 else 1
        sda_master_low = bool(oe & 0x01)
        pull = slave.step(scl, 0 if (sda_master_low or slave.pull_sda) else 1)
        sda = 0 if (sda_master_low or pull) else 1
        hh.uio_in = (hh.uio_in & ~0x03) | (scl << 1) | sda
    h.after_tick = bus
    h.uio_in = 0x03
    await h.load(0, example("i2c_master.pio"))
    await h.xfer(cmd_run(arm=True))
    await h.start(0)
    await h.xfer(cmd_mbox(0, 0xA0))   # address 0x50, write
    await h.xfer(cmd_mbox(0, 0x3C))   # data
    await h.tick(1500)
    assert slave.events == ["START", "STOP"], slave.events
    assert slave.bytes == [0xA0, 0x3C], slave.bytes
    entries = await read_trace(h, 2)
    assert [e & 1 for e in entries] == [0, 0], "both bytes ACKed"
    assert h.uio_out & 0x03 == 0, "open-drain lines never drive high"
