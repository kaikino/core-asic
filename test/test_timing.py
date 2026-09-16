# SPDX-License-Identifier: Apache-2.0
"""Sub-clock timing: TDC calibration and edge timing, DTC edge placement, and
the engine instructions that use them.

The RTL run simulates the delay lines at TDLY_PS picoseconds per stage (224
by default, see test/Makefile); the reference model uses the same figure, so
fine times are compared exactly.  On zero-delay netlists (gate level, FPGA)
the chains collapse and these tests check only the plumbing.
"""
import os

import cocotb
from cocotb.triggers import ReadOnly, RisingEdge, Timer

from common import example, read_trace, setup  # noqa: F401
from proto_asm import assemble_program
from proto_ref import (CLOCK_PS, PAD_EDGE_OFFSET_PS, READ_MBOX, READ_STATUS, READ_TIMING,
                       TDLY_PS, TDLY_STAGES, cmd_dtc, cmd_perm, cmd_run, cmd_tdc, fine_count)

CAL = 13  # TDC source: the calibration toggle


@cocotb.test()
async def test_tdc_calibration(dut):
    """The calibration source reports how many stages span one clock period."""
    h = await setup(dut)
    # Load the reader program first: a toggling chain is slow to simulate.
    prog = assemble_program("in r0, TDC0\nmbox r0\ndone\nhalt").words
    await h.load(0, prog)
    await h.xfer(cmd_tdc(0, CAL))
    await h.tick(8)
    word = await h.read(READ_TIMING)
    stages_per_period = fine_count(CLOCK_PS)
    assert word >> 24 == TDLY_STAGES, "chain length readback"
    assert word & 0xFF == stages_per_period, hex(word)
    if TDLY_PS:
        assert stages_per_period == 25000 // TDLY_PS
    # An engine can read the same value.
    await h.start(0)
    await h.wait_idle(0)
    assert (await h.read(READ_MBOX)) & 0xFF == stages_per_period
    await h.xfer(cmd_tdc(0, 15))  # source off


@cocotb.test()
async def test_tdc_edge_time_and_trace(dut):
    """An input edge is timed within the period and captured as a kind-2 entry."""
    h = await setup(dut)
    await h.xfer(cmd_tdc(0, 8, trace=True))    # TARGET_IN0
    await h.xfer(cmd_tdc(1, 12, trace=True))   # TRIGGER_IN
    await h.xfer(cmd_run(arm=True, ts_reset=True))
    await h.tick(4)
    h.ui_target = 0b00001                       # IN0 rises 1 ns after the edge
    await h.tick(6)
    h.ui_target = 0b10001                       # TRIGGER_IN rises
    await h.tick(6)
    h.ui_target = 0b10000                       # IN0 falls
    await h.tick(6)
    expect = fine_count(CLOCK_PS - PAD_EDGE_OFFSET_PS)
    word = await h.read(READ_TIMING)
    assert word & 0xFF == expect and (word >> 16) & 1 == 0, f"IN0 fine/level {word:08x}"
    assert (word >> 8) & 0xFF == expect and (word >> 17) & 1 == 1, f"TRIGGER fine/level {word:08x}"
    status = await h.read(READ_STATUS)
    n = (status >> 16) & 0x3F
    entries = await read_trace(h, n)
    assert entries == h.model.trace[:n]
    kinds = [(e >> 14) & 3 for e in entries]
    assert kinds == [2, 2, 2], kinds
    assert [(e >> 13) & 1 for e in entries] == [0, 1, 0], "channels"
    assert [(e >> 12) & 1 for e in entries] == [1, 1, 0], "levels"
    assert all((e >> 4) & 0xFF == expect for e in entries), "fine times"
    # An engine reads the latched value after waiting for the edge.
    prog = assemble_program("wait RISE, IN0\nin r0, TDC0\nmbox r0\ndone\nhalt").words
    await h.load(1, prog)
    await h.xfer(cmd_perm(1, 0, 0))
    await h.start(1)
    await h.tick(3)
    h.ui_target = 0b10001
    await h.tick(6)
    await h.wait_idle(1)
    assert (await h.read(READ_MBOX)) >> 8 & 0xFF == expect


@cocotb.test()
async def test_dtc_edge_placement(dut):
    """A DTC channel moves an output edge by the programmed number of stages."""
    h = await setup(dut)
    tap = 60
    await h.xfer(cmd_dtc(0, pin=2, enable=True, tap=tap))
    prog = assemble_program("setp UO_OE, 2, 1\nl: setp UO, 2, 1\ndelay 4\nsetp UO, 2, 0\ndelay 4\njmp l").words
    await h.load(0, prog)
    await h.start(0)
    # Watch TARGET_OUT2 (uo_out[3]) with fine time resolution across the edge
    # on which the program raises it: sample the pin at several offsets.
    seen = {}
    for _ in range(40):
        await RisingEdge(dut.clk)
        await ReadOnly()
        at_edge = (int(dut.uo_out.value) >> 3) & 1
        await Timer(tap * TDLY_PS // 2 if TDLY_PS else 1, unit="ps")
        before = (int(dut.uo_out.value) >> 3) & 1
        await Timer((tap * TDLY_PS // 2 + 2 * TDLY_PS) if TDLY_PS else 1, unit="ps")
        after = (int(dut.uo_out.value) >> 3) & 1
        seen[(at_edge, before, after)] = seen.get((at_edge, before, after), 0) + 1
        await Timer(1, unit="ps")
        uio_in, ui_in = h.uio_in, h.ui_in
        h.model.step(uio_in, ui_in, None)
        h.cycle += 1
    if TDLY_PS:
        # The rising edge appears only after tap*TDLY_PS: (0, 0, 1) must occur,
        # and the pin is never high right at the launching clock edge.
        assert (0, 0, 1) in seen, seen
        assert (1, 1, 1) in seen, seen
    dut._log.info(f"edge placement samples: {seen}")
    await h.stop(0)


@cocotb.test()
async def test_engine_sets_dtc_tap(dut):
    """DTCW writes the tap from a program; the model's pad view follows."""
    h = await setup(dut)
    await h.xfer(cmd_dtc(1, pin=0, enable=True, tap=0))
    prog = assemble_program(
        "setp UO_OE, 0, 1\nmovi r0, 120\ndtcw r0, 1\nmovi r1, 8\n"
        "l: setp UO, 0, 1\ndelay 2\nsetp UO, 0, 0\ndelay 2\ndjnz r1, l\ndone\nhalt").words
    await h.load(0, prog)
    await h.start(0)
    await h.wait_idle(0)
    assert h.model.dtc_tap[1] == 120
