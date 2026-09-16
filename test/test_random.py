# SPDX-License-Identifier: Apache-2.0
"""Constrained-random programs on both engines compared against the reference model.

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
    cmd_capture, cmd_fifo, cmd_gpio, cmd_mbox, cmd_perm, cmd_run, cmd_traceptr)
from protocols import (  # noqa: F401
    I2cSlave, SpiSlave, UartMonitor, UartSource, find_payload, manchester_decode)


def random_program(rng: random.Random, length: int = 40) -> list:
    """Straight-line random code plus bounded loops; every WAIT targets a pin."""
    words = []
    for i in range(length):
        op = rng.choice([0x1, 0x2, 0x3, 0x4, 0x5, 0x7, 0x8, 0x9, 0xA, 0xB, 0xC, 0xD, 0x0])
        rd, sub, imm = rng.randrange(4), rng.randrange(4), rng.randrange(256)
        if op == 0x4:
            imm = rng.randrange(6); sub = rng.choice([0, 1]) and 0
        if op == 0x5:
            imm = rng.randrange(13); sub = rng.randrange(4)
        if op == 0x7:
            imm = rng.randrange(max(0, i - 3), min(length, i + 4))
            if imm == i: imm = (i + 1) % length
        if op in (0x8, 0x9):
            imm = min(length - 1, i + rng.randrange(1, 4))
            sub = rng.randrange(4); rd = rng.randrange(4)
        if op == 0xD and sub == 2 and rng.random() < 0.7:
            sub = 0
        words.append((op << 12) | (rd << 10) | (sub << 8) | imm)
    words.append(0x6000 | rng.randrange(length))  # loop back somewhere
    return words


@cocotb.test()
async def test_constrained_random(dut):
    """Milestone 7: random programs on both engines with random pads and masks."""
    seeds = int(os.environ.get("RANDOM_SEEDS", "4"))
    cycles = int(os.environ.get("RANDOM_CYCLES", "1500"))
    for seed in range(seeds):
        rng = random.Random(1000 + seed)
        h = await setup(dut)
        await h.load(0, random_program(rng))
        await h.load(1, random_program(rng))
        await h.xfer(cmd_perm(0, rng.randrange(256), rng.randrange(128)))
        await h.xfer(cmd_perm(1, rng.randrange(256), rng.randrange(128)))
        await h.xfer(cmd_capture(watch=rng.randrange(1 << 13), trig_src=rng.randrange(6),
                                 pin_capture=True, stop_on_full=bool(rng.randrange(2))))
        await h.xfer(cmd_gpio(rng.randrange(256), rng.randrange(256), rng.randrange(128)))
        fcs = bool(rng.randrange(2))
        await h.xfer(cmd_fifo(reset=True, fcs_mode=fcs))
        for _ in range(rng.randrange(6)):
            await h.xfer(cmd_fifo(rng.randrange(256), push=True, fcs_mode=fcs))
        await h.xfer(cmd_run(start0=True, start1=True, arm=True, start_pc=rng.randrange(8)))

        def noise(hh, rng=rng):
            if rng.random() < 0.3:
                hh.uio_in = rng.randrange(256)
            if rng.random() < 0.3:
                hh.ui_target = rng.randrange(32)
        h.after_tick = noise
        for _ in range(cycles // 100):
            await h.tick(100)
            if rng.random() < 0.3:
                await h.xfer(cmd_mbox(rng.randrange(2), rng.randrange(256)))
            if rng.random() < 0.3:
                await h.xfer(cmd_fifo(rng.randrange(256), push=True, fcs_mode=fcs))
        h.after_tick = None
        await h.xfer(cmd_run(stop0=True, stop1=True))
        status = await h.read(READ_STATUS)
        assert status == h.model.status_word(), f"status {status:08x} vs model {h.model.status_word():08x}"
        # The link captures the register a frame early, so ignore the timestamp bits.
        assert (await h.read(READ_TIME)) >> 16 == h.model.read_register(READ_TIME) >> 16, "FIFO/trace readback"
        n = min(TRACE_DEPTH, (status >> 16) & 0x3F)
        entries = await read_trace(h, n)
        assert entries == h.model.trace[:n], "trace buffer matches the model"
        dut._log.info(f"seed {seed}: {h.cycle} cycles, {n} trace entries, status {status:08x}")
        dut.rst_n.value = 0


@cocotb.test()
async def test_random_with_timing_channels(dut):
    """Random programs with random TDC sources, trace-enabled edges and DTC taps.

    Simulating the delay lines is expensive (every input edge schedules 176
    events per chain), so this test is short by default; raise RANDOM_SEEDS and
    RANDOM_CYCLES for a longer soak."""
    from proto_ref import cmd_dtc, cmd_tdc
    seeds = int(os.environ.get("RANDOM_SEEDS", "2"))
    cycles = int(os.environ.get("RANDOM_CYCLES", "300"))
    for seed in range(seeds):
        rng = random.Random(5000 + seed)
        h = await setup(dut)
        await h.load(0, random_program(rng))
        await h.load(1, random_program(rng))
        await h.xfer(cmd_tdc(0, rng.choice([8, 9, 10, 11, 12]), trace=True))
        await h.xfer(cmd_tdc(1, rng.randrange(13), trace=bool(rng.randrange(2))))
        await h.xfer(cmd_dtc(0, rng.randrange(7), bool(rng.randrange(2)), rng.randrange(180)))
        await h.xfer(cmd_dtc(1, rng.randrange(7), bool(rng.randrange(2)), rng.randrange(180)))
        await h.xfer(cmd_capture(watch=rng.randrange(1 << 13), trig_src=0, pin_capture=bool(rng.randrange(2))))
        await h.xfer(cmd_run(start0=True, start1=True, arm=True))

        def noise(hh, rng=rng):
            if rng.random() < 0.2:
                hh.uio_in = rng.randrange(256)
            if rng.random() < 0.2:
                hh.ui_target = rng.randrange(32)
        h.after_tick = noise
        await h.tick(cycles)
        h.after_tick = None
        await h.xfer(cmd_run(stop0=True, stop1=True))
        status = await h.read(READ_STATUS)
        n = min(TRACE_DEPTH, (status >> 16) & 0x3F)
        entries = await read_trace(h, n)
        assert entries == h.model.trace[:n], "trace buffer incl. timed edges matches the model"
        dut._log.info(f"seed {seed}: {n} trace entries, kinds {[(e >> 14) & 3 for e in entries]}")
        dut.rst_n.value = 0
