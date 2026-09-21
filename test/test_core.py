# SPDX-License-Identifier: Apache-2.0
"""Core control-plane, engine, arbitration, mailbox and capture tests.

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
    cmd_capture, cmd_crc_config, cmd_fifo, cmd_gpio, cmd_mbox, cmd_perm, cmd_run, cmd_traceptr)
from protocols import (  # noqa: F401
    I2cSlave, SpiSlave, UartMonitor, UartSource, find_payload, manchester_decode)


@cocotb.test()
async def test_reset_gpio_and_readback(dut):
    """Milestone 2: host GPIO drive plus ID/status readback over the link."""
    h = await setup(dut)
    assert h.uio_oe == 0 and h.uo_out == 0
    assert await h.read(READ_ID) == 0x50494F31
    await h.xfer(cmd_gpio(uio_oe=0x0F, uio_data=0xA5, uo_data=0x55))
    await h.tick(2)
    assert h.uio_oe == 0x0F and h.uio_out == 0xA5 and (h.uo_out >> 1) == 0x55
    status = await h.read(READ_STATUS)
    assert status >> 24 == 0x10 and status & 0xFFFF == 0
    await h.xfer(cmd_run(ts_reset=True))
    before = h.model.timestamp
    t = await h.read(READ_TIME)
    # The value is captured while CS is high before the frame that returns it.
    assert before < (t & 0xFFFF) < h.model.timestamp, "timestamp readback"


@cocotb.test()
async def test_program_load_run_and_write_lock(dut):
    """Milestone 3: program load, start/stop, and writes ignored while running."""
    h = await setup(dut)
    await h.load(0, example("gpio_blink.pio"))
    await h.start(0)
    await h.tick(200)
    assert h.uio_oe == 0xFF, "engine 0 should own every GPIO"
    # A write while running must be ignored (the model ignores it too).
    await h.load(0, [0xE000], base=4)
    h.ui_target = 0b01010
    await h.tick(100)
    assert (h.uo_out >> 1) & 0x0F == 0b1010, "AUX inputs mirrored to TARGET_OUT"
    await h.stop(0)
    await h.tick(5)
    assert h.uio_oe == 0
    # Now the write goes through: halt at address 4 stops the engine at once.
    await h.load(0, [0xE000], base=4)
    await h.start(0)
    status = await h.wait_idle(0)
    assert status & 0b11 == 0


@cocotb.test()
async def test_dual_engine_collision_and_permissions(dut):
    """Milestone 4: concurrent drives, hi-Z on collision, sticky faults, masks."""
    h = await setup(dut)
    prog0 = assemble_program("movi r0,0x03\nout r0,UIO_OE,0x03\nout r0,UIO,0x03\nloop: jmp loop").words
    prog1 = assemble_program("movi r0,0x06\nout r0,UIO_OE,0x06\nmovi r0,0x04\nout r0,UIO,0x06\nloop: jmp loop").words
    await h.load(0, prog0)
    await h.load(1, prog1)
    await h.xfer(cmd_run(start0=True, start1=True))
    await h.tick(20)
    assert h.uio_oe == 0x05, f"GPIO1 must be high-impedance, oe={h.uio_oe:02x}"
    assert h.uio_out & 0x05 == 0x05, "GPIO0 from engine 0, GPIO2 from engine 1"
    status = await h.read(READ_STATUS)
    assert status & 0x10, "collision fault latched"
    await h.xfer(cmd_run(stop0=True, stop1=True))
    await h.xfer(cmd_run(clear_faults=True))
    assert not (await h.read(READ_STATUS)) & 0x30
    # Restrict engine 1 to GPIO2: no collision, but a permission fault.
    await h.xfer(cmd_perm(1, uio_mask=0x04, uo_mask=0x00))
    await h.xfer(cmd_run(start0=True, start1=True))
    await h.tick(20)
    assert h.uio_oe == 0x07 and h.uio_out == 0x07
    status = await h.read(READ_STATUS)
    assert status & 0x20 and not status & 0x10, f"status={status:08x}"


@cocotb.test()
async def test_mailbox_done_and_illegal(dut):
    """Mailbox round trip, DONE flag, and illegal-opcode fault."""
    h = await setup(dut)
    prog = assemble_program("wait HIGH, MBOX_IN\nin r0, MBOX\nmov r1, r0\nadd r0, r1\nmbox r0\ndone\n.word 0xF000").words
    await h.load(1, prog)
    await h.start(1)
    await h.xfer(cmd_mbox(1, 0x21))
    status = await h.wait_idle(1)
    assert status & 0x08, "engine 1 DONE"
    assert status & 0x80, "engine 1 illegal opcode fault"
    assert status & 0x800, "engine 1 mailbox pending"
    assert (await h.read(READ_MBOX) >> 8) & 0xFF == 0x42
    await h.xfer(cmd_run(ack_rx1=True, clear_done=True))
    assert not (await h.read(READ_STATUS)) & 0x808


@cocotb.test()
async def test_trigger_capture(dut):
    """Milestone 4: TRIGGER_IN-armed, timestamped pin-change capture."""
    h = await setup(dut)
    await h.xfer(cmd_capture(watch=0x0F00, trig_src=1, pin_capture=True))
    await h.xfer(cmd_run(arm=True, ts_reset=True))
    h.ui_target = 0b00101       # changes before the trigger are not captured
    await h.tick(10)
    h.ui_target = 0b10101       # TRIGGER_IN rising edge
    await h.tick(10)
    for v in (0b10110, 0b10011, 0b11111, 0b10000):
        h.ui_target = v
        await h.tick(7)
    status = await h.read(READ_STATUS)
    assert status & 0x2000 and (status >> 16) & 0x3F == 4, f"status={status:08x}"
    entries = await read_trace(h, 4)
    assert entries == h.model.trace[:4], "trace entries incl. timestamps match the model"
    assert [(e >> 8) & 0xF for e in entries] == [0b0110, 0b0011, 0b1111, 0b0000]
    assert all(e & (1 << 14) for e in entries), "kind = pin capture"
    stamps = [e >> 16 for e in entries]
    assert stamps[1] - stamps[0] == 7 and stamps[2] - stamps[1] == 7


@cocotb.test()
async def test_fifo_and_crc(dut):
    """Host FIFO pops, hardware FCS, and the explicit CRC-32 instructions."""
    import zlib
    h = await setup(dut)
    data = [0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39]  # "123456789"
    # Engine 1 pops everything into the trace: 9 bytes, then the 4 FCS bytes.
    prog = assemble_program("l: pop r0\nmovc r1\njz r1, end\ntrace r0\njmp l\nend: done\nhalt").words
    await h.load(1, prog)
    await h.xfer(cmd_fifo(reset=True, fcs_mode=True))
    for b in data:
        await h.xfer(cmd_fifo(b, push=True, fcs_mode=True))
    assert ((await h.read(READ_TIME)) >> 22) & 0xFF == len(data), "FIFO level readback"
    await h.xfer(cmd_run(arm=True, start1=True))
    status = await h.wait_idle(1)
    assert status & 0x08, "DONE"
    entries = await read_trace(h, (status >> 16) & 0x3F)
    got = [e & 0xFF for e in entries]
    fcs = zlib.crc32(bytes(data)) & 0xFFFFFFFF
    assert got == data + [(fcs >> (8 * k)) & 0xFF for k in range(4)], [hex(x) for x in got]
    assert fcs == 0xCBF43926, "CRC-32 check value"
    # Explicit CRC: fold the same bytes with CRCU and read the FCS with CRCB.
    prog = assemble_program(
        "crci\nmovi r0, 0x31\nmovi r2, 9\nl: crcu r0\nmovi r1, 1\nadd r0, r1\ndjnz r2, l\n"
        "crcb r3, 0\ntrace r3\ncrcb r3, 1\ntrace r3\ncrcb r3, 2\ntrace r3\ncrcb r3, 3\ntrace r3\ndone\nhalt").words
    await h.load(0, prog)
    await h.xfer(cmd_run(arm=True, start0=True))
    status = await h.wait_idle(0)
    entries = await read_trace(h, 4)
    assert [e & 0xFF for e in entries] == [0x26, 0x39, 0xF4, 0xCB]


@cocotb.test()
async def test_programmable_crc(dut):
    """CRC-16/USB through the FIFO's FCS mode and CRC-15/CAN through CRCBIT."""
    h = await setup(dut)
    data = [0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39]
    # CRC-16/USB: reflected polynomial 0xA001, init and final XOR 0xFFFF, check 0xB4C8.
    for f in cmd_crc_config(0xA001, 0xFFFF, 0xFFFF):
        await h.xfer(f)
    prog = assemble_program("l: pop r0\nmovc r1\njz r1, end\ntrace r0\njmp l\nend: done\nhalt").words
    await h.load(0, prog)
    await h.xfer(cmd_fifo(reset=True, fcs_mode=True))
    for b in data:
        await h.xfer(cmd_fifo(b, push=True, fcs_mode=True))
    await h.xfer(cmd_run(arm=True, start0=True))
    status = await h.wait_idle(0)
    entries = await read_trace(h, (status >> 16) & 0x3F)
    got = [e & 0xFF for e in entries]
    assert got == data + [0xC8, 0xB4, 0x00, 0x00], [hex(x) for x in got]
    # CRC-15/CAN: polynomial 0x4599 MSB-first == reflected 0x4CD1 with bits fed MSB
    # first; check value 0x059E, which the reflected register holds bit-reversed.
    for f in cmd_crc_config(0x4CD1, 0x0000, 0x0000):
        await h.xfer(f)
    prog = assemble_program(
        "crci\nmovi r0, 0x31\nmovi r2, 9\n"
        "byte: movi r3, 8\n"
        "bit: shl r0\ncrcbit\ndjnz r3, bit\n"      # feed the byte MSB first
        "djnz r2, next\njmp fin\n"
        "next: wait HIGH, MBOX_IN\nin r0, MBOX\njmp byte\n"
        "fin: crcb r3, 0\ntrace r3\ncrcb r3, 1\ntrace r3\ndone\nhalt").words
    await h.load(1, prog)
    await h.xfer(cmd_run(arm=True))
    await h.start(1)
    for b in data[1:]:
        await h.xfer(cmd_mbox(1, b))
        await h.tick(30)
    status = await h.wait_idle(1)
    entries = await read_trace(h, 2)
    reflected = int(format(0x059E, "015b")[::-1], 2)
    assert [e & 0xFF for e in entries] == [reflected & 0xFF, (reflected >> 8) & 0x7F], [hex(e & 0xFF) for e in entries]


@cocotb.test()
async def test_protocol_aware_trigger(dut):
    """Engine 1 decodes I2C and its TRACE on a matching address triggers pin capture."""
    from protocols import I2cMaster
    h = await setup(dut)
    # Two transactions: one to 0x2A (ignored), then one to 0x50 (triggers).
    master = I2cMaster([("start",), ("write", 0x54), ("write", 0x01), ("stop",),
                        ("start",), ("write", 0xA0), ("write", 0x03), ("stop",)], half_period=40)

    def bus(hh):
        scl_low, sda_low = master.step(1)
        hh.uio_in = (hh.uio_in & ~0x03) | ((0 if scl_low else 1) << 1) | (0 if sda_low else 1)
    h.uio_in = 0x03
    await h.load(1, example("i2c_watch.pio"))
    await h.xfer(cmd_perm(1, 0, 0))
    await h.xfer(cmd_capture(watch=0x0003, trig_src=4, pin_capture=True))
    await h.xfer(cmd_run(arm=True, start1=True, ts_reset=True))
    h.after_tick = bus
    await h.tick(40 * 2 * 9 * 5 + 800)
    assert master.done
    status = await h.read(READ_STATUS)
    assert status & 0x2000, "capture triggered"
    n = (status >> 16) & 0x3F
    entries = await read_trace(h, n)
    assert entries == h.model.trace[:n]
    kinds = [(e >> 14) & 3 for e in entries]
    assert kinds[0] == 0 and (entries[0] & 0xFF) == 0xA0, "first entry is the matching address event"
    assert all(k == 1 for k in kinds[1:]) and len(kinds) > 10, "then captured bus edges"
    # Every captured edge belongs to the second transaction: the first
    # transaction ended before the trigger, so no entry precedes the event.
    assert entries[0] >> 16 < min(e >> 16 for e in entries[1:])
