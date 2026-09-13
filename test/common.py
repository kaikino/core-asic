# SPDX-License-Identifier: Apache-2.0
"""Shared helpers for the cocotb suite: DUT setup and trace readback."""
import os
import sys

import cocotb
from cocotb.clock import Clock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

from proto_asm import assemble_program  # noqa: E402
from proto_host import Harness  # noqa: E402
from proto_ref import READ_TRACE, cmd_traceptr  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples")


def example(name: str, **subst) -> list:
    """Assemble examples/<name>, optionally overriding `.equ` constants."""
    text = open(os.path.join(EXAMPLES, name)).read()
    for key, value in subst.items():
        text = text.replace(f".equ {key} ", f".equ {key} {value} ;")
    return assemble_program(text).words


async def setup(dut, compare=True) -> Harness:
    cocotb.start_soon(Clock(dut.clk, 25, unit="ns").start())
    h = Harness(dut, compare=compare)
    await h.reset()
    await h.tick(3)
    return h


async def read_trace(h: Harness, count: int) -> list:
    entries = []
    for i in range(count):
        await h.xfer(cmd_traceptr(i))
        entries.append(await h.read(READ_TRACE))
    return entries
