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


@cocotb.test()
async def test_jtag_idcode(dut):
    """JTAG master reads a TAP's IDCODE through the DR path."""
    from protocols import JtagTap
    h = await setup(dut)
    tap = JtagTap(idcode=0x2BA01477)

    def peer(hh):
        o = hh.uio_out
        tdo = tap.step(o & 1, (o >> 1) & 1, (o >> 2) & 1)
        hh.uio_in = (hh.uio_in & ~0x08) | (tdo << 3)
    h.after_tick = peer
    await h.load(0, example("jtag_idcode.pio"))
    await h.xfer(cmd_run(arm=True))
    await h.start(0)
    await h.tick(1500)
    status = await h.wait_idle(0)
    assert status & 0x04, "DONE"
    entries = await read_trace(h, 4)
    assert [e & 0xFF for e in entries] == [0x77, 0x14, 0xA0, 0x2B], [hex(e & 0xFF) for e in entries]
    assert "CAPDR" in tap.visited and tap.visited[-1] == "IDLE", tap.visited[-6:]


@cocotb.test()
async def test_swd_dpidr(dut):
    """SWD host performs the JTAG-to-SWD switch and reads DPIDR."""
    from protocols import SwdTarget
    h = await setup(dut)
    target = SwdTarget(dpidr=0x2BA01477)

    def bus(hh):
        clk = hh.uio_out & 1
        host_drives = bool(hh.uio_oe & 0x02)
        host_val = (hh.uio_out >> 1) & 1
        line = host_val if host_drives else 1
        driving, val = target.step(clk, line)
        if driving and not host_drives:
            line = val
        hh.uio_in = (hh.uio_in & ~0x02) | (line << 1)
    h.after_tick = bus
    h.uio_in = 0x02
    await h.load(0, example("swd_dpidr.pio"))
    await h.xfer(cmd_run(arm=True))
    await h.start(0)
    await h.tick(3000)
    status = await h.wait_idle(0)
    assert status & 0x04, "DONE"
    assert target.requests == [0xA5], target.requests
    entries = await read_trace(h, 6)
    vals = [e & 0xFF for e in entries]
    assert vals[0] == 0b001, f"ACK {vals[0]:03b}"
    assert vals[1:5] == [0x77, 0x14, 0xA0, 0x2B], [hex(v) for v in vals[1:5]]
    assert vals[5] == (0x02 if bin(0x2BA01477).count("1") & 1 else 0), "parity"


@cocotb.test()
async def test_ps2_device(dut):
    """PS/2 device frames with odd parity decoded by a host monitor."""
    from protocols import Ps2Host
    h = await setup(dut)
    host = Ps2Host()

    def bus(hh):
        oe = hh.uio_oe
        clk, data = 0 if oe & 1 else 1, 0 if oe & 2 else 1
        host.step(clk, data)
        hh.uio_in = (hh.uio_in & ~0x03) | (data << 1) | clk
    h.after_tick = bus
    h.uio_in = 0x03
    await h.load(0, example("ps2_device.pio"))
    await h.start(0)
    codes = [0x1C, 0xF0, 0x1C, 0x5A]   # 'A' make, break, and Enter
    for c in codes:
        await h.xfer(cmd_mbox(0, c))
        await h.tick(400)
    assert host.bytes == codes, host.bytes
    assert not host.errors, host.errors
    assert h.uio_out & 0x03 == 0, "open-drain lines never drive high"


@cocotb.test()
async def test_can_transmit(dut):
    """CAN 2.0A data frame with bit stuffing and CRC-15, acknowledged by a monitor."""
    from proto_ref import cmd_crc_config, cmd_fifo
    from protocols import CanMonitor, can_frame_bits
    h = await setup(dut)
    bit_clocks = 60
    mon = CanMonitor(bit_clocks)

    def bus(hh):
        tx = hh.uio_out & 1 if hh.uio_oe & 1 else 1
        bus_level = 0 if mon.ack_active() else tx     # wired-AND with the monitor's ACK
        mon.step(bus_level)
        hh.uio_in = (hh.uio_in & ~0x02) | (bus_level << 1)
    h.after_tick = bus
    h.uio_in = 0x02
    for f in cmd_crc_config(0x4CD1, 0, 0):
        await h.xfer(f)
    ident, data = 0x123, bytes([0xDE, 0xAD, 0x7F, 0x00])   # 0x7F/0x00 provoke stuffing
    await h.xfer(cmd_fifo(reset=True))
    for b in can_frame_bits(ident, data):
        await h.xfer(cmd_fifo(b, push=True))
    await h.load(0, example("can_tx.pio"))
    await h.xfer(cmd_run(arm=True))
    await h.start(0)
    await h.tick(140 * bit_clocks)
    status = await h.wait_idle(0)
    assert status & 0x04, "DONE"
    assert mon.frames, "no frame decoded"
    f = mon.frames[0]
    assert (f["id"], f["dlc"], f["data"]) == (ident, len(data), data), f
    assert f["crc_ok"], f
    assert f["rtr"] == 0 and f["ide"] == 0 and f["crc_delim"] == 1 and f["ack_delim"] == 1, f
    assert f["ack_slot"] == 0, "monitor's ACK seen on the bus"
    assert f["stuff_bits"] >= 1, "stuffing exercised"
    entries = await read_trace(h, 1)
    assert entries[0] & 0xFF == 0, "transmitter saw the ACK"


@cocotb.test()
async def test_usb_ls_data_packet(dut):
    """Low-speed USB DATA packet: SYNC, PID, payload, CRC-16, NRZI, bit stuffing, EOP."""
    from proto_ref import cmd_crc_config, cmd_fifo
    from protocols import UsbLsDecoder
    h = await setup(dut)
    bit_clocks = 27
    dec = UsbLsDecoder(bit_clocks)

    def bus(hh):
        oe, o = hh.uio_oe, hh.uio_out
        dp = (o & 1) if oe & 1 else 0
        dm = ((o >> 1) & 1) if oe & 2 else 1      # idle J through the pull-up on D-
        dec.step(dp, dm)
    h.after_tick = bus
    for f in cmd_crc_config(0xA001, 0xFFFF, 0xFFFF):
        await h.xfer(f)
    # First 8 bytes of a device descriptor plus two 0xFF bytes that force bit stuffing.
    payload = bytes([0x12, 0x01, 0x10, 0x01, 0x00, 0x00, 0x00, 0x08, 0xFF, 0xFF])
    await h.xfer(cmd_fifo(reset=True))
    for b in payload:
        await h.xfer(cmd_fifo(b, push=True))
    await h.load(0, example("usb_ls_tx.pio"))
    await h.xfer(cmd_run(arm=True))
    await h.start(0)
    await h.xfer(cmd_mbox(0, 0xC3))                      # DATA0
    await h.tick(150 * bit_clocks)
    status = await h.wait_idle(0)
    assert status & 0x04, "DONE"
    assert dec.packets, "no packet decoded"
    p = dec.packets[0]
    assert p["sync_ok"] and not p["stuff_error"], p
    assert p["pid"] == 0xC3, hex(p["pid"])
    assert p["payload"][:len(payload)] == payload, p["payload"].hex()
    c = 0xFFFF                                            # CRC-16/USB, sent LSB first
    for byte in payload:
        c ^= byte
        for _ in range(8):
            c = (c >> 1) ^ (0xA001 if c & 1 else 0)
    c ^= 0xFFFF
    assert list(p["payload"][len(payload):]) == [c & 0xFF, c >> 8], (p["payload"].hex(), hex(c))
