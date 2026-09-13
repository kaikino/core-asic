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
from protocols import (  # noqa: F401
    I2cSlave, SpiSlave, UartMonitor, UartSource, find_payload, manchester_decode)


@cocotb.test()
async def test_manchester_loopback(dut):
    """Milestone 6: 10 Mbit/s Manchester TX on engine 0 decoded by engine 1."""
    h = await setup(dut)
    samples = []

    def loop(hh):
        bit = (hh.uo_out >> 1) & 1
        samples.append(bit)
        hh.ui_target = (hh.ui_target & ~1) | bit
    h.after_tick = loop
    await h.load(0, example("manchester_tx.pio"))
    await h.load(1, example("manchester_rx.pio"))
    await h.xfer(cmd_perm(1, uio_mask=0, uo_mask=0))
    await h.xfer(cmd_run(arm=True))
    await h.xfer(cmd_run(start1=True))
    del samples[:]
    await h.xfer(cmd_run(start0=True))
    await h.tick(700)
    status = await h.wait_idle(0)
    assert status & 0x04, "TX engine signals DONE"
    # Independent software decode of the TX waveform.
    tx_en_on = samples.index(1)
    burst = samples[tx_en_on:]
    edges = [i for i in range(1, len(burst)) if burst[i] != burst[i - 1]]
    gaps = [b - a for a, b in zip(edges, edges[1:])]
    assert set(gaps[: 8 * 14 - 1]) <= {2, 4}, f"symbol timing {sorted(set(gaps))}"
    bits = manchester_decode(burst)
    payload = find_payload(bits)
    dut._log.info(f"tx samples={len(samples)} start={tx_en_on} edges={len(edges)} gaps={sorted(set(gaps))} "
                  f"bits={''.join(map(str, bits[:72]))}")
    assert payload[:4] == [0x12, 0x34, 0x56, 0x78], payload
    # Engine 1's decoder traced the same stream.
    count = ((await h.read(READ_STATUS)) >> 16) & 0x3F
    entries = await read_trace(h, count)
    rx_bits = [b for e in entries for b in [(e >> (7 - i)) & 1 for i in range(8)]]
    assert find_payload(rx_bits)[:4] == [0x12, 0x34, 0x56, 0x78], find_payload(rx_bits)
