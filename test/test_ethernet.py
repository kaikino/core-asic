# SPDX-License-Identifier: Apache-2.0
"""10 Mbit/s Manchester (10BASE-T style) transmit/receive demonstration.

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
from proto_ref import cmd_fifo  # noqa: F401
from protocols import (  # noqa: F401
    I2cSlave, SpiSlave, UartMonitor, UartSource, find_payload, frame_ok, manchester_decode)


# A minimum-size Ethernet frame: broadcast dst, a locally administered src,
# a test EtherType and a 46-byte payload; the FCS is appended by the chip.
FRAME = ([0xFF] * 6 + [0x02, 0x00, 0x00, 0x00, 0x00, 0x01] + [0x88, 0xB5]
         + [(0x20 + i) & 0xFF for i in range(46)])


@cocotb.test()
async def test_manchester_frame_loopback(dut):
    """Milestone 6: a 64-byte frame from the FIFO with hardware FCS at 10 Mbit/s,
    decoded from the waveform and by the second engine."""
    h = await setup(dut)
    samples = []
    tx_en = []

    def loop(hh):
        bit = (hh.uo_out >> 1) & 1
        samples.append(bit)
        tx_en.append((hh.uo_out >> 2) & 1)
        hh.ui_target = (hh.ui_target & ~1) | bit
    h.after_tick = loop
    await h.load(0, example("manchester_tx.pio"))
    await h.load(1, example("manchester_rx.pio"))
    await h.xfer(cmd_perm(1, uio_mask=0, uo_mask=0))
    await h.xfer(cmd_fifo(reset=True, fcs_mode=True))
    for b in FRAME:
        await h.xfer(cmd_fifo(b, push=True, fcs_mode=True))
    await h.xfer(cmd_run(arm=True))
    await h.xfer(cmd_run(start1=True))
    del samples[:]
    del tx_en[:]
    await h.xfer(cmd_run(start0=True))
    await h.xfer(cmd_mbox(0, (len(FRAME) + 4) // 2))
    await h.tick(8 * (len(FRAME) + 4 + 8) * 4 + 200)
    status = await h.wait_idle(0)
    assert status & 0x04, "TX engine signals DONE"
    # Independent software decode of the TX waveform, aligned on TX_EN.
    tx_en_on = tx_en.index(1) + 1
    burst = samples[tx_en_on:]
    edges = [i for i in range(1, len(burst)) if burst[i] != burst[i - 1]]
    gaps = [b - a for a, b in zip(edges, edges[1:])]
    n_bits = 8 * (8 + len(FRAME) + 4)
    assert set(gaps[: n_bits - 1]) <= {2, 4}, f"symbol timing {sorted(set(gaps))}"
    bits = manchester_decode(burst)
    frame = find_payload(bits)[: len(FRAME) + 4]
    dut._log.info(f"tx samples={len(samples)} edges={len(edges)} gaps={sorted(set(gaps))} "
                  f"frame={len(frame)} bytes")
    assert frame[: len(FRAME)] == FRAME, "frame bytes"
    assert frame_ok(frame), f"FCS {[hex(x) for x in frame[-4:]]}"
    # Engine 1's decoder traced the first 32 bytes of the same stream.
    count = ((await h.read(READ_STATUS)) >> 16) & 0x3F
    entries = await read_trace(h, count)
    rx_bits = [b for e in entries for b in [(e >> i) & 1 for i in range(8)]]
    rx = find_payload(rx_bits)
    assert rx[:16] == FRAME[:16], rx
