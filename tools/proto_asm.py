#!/usr/bin/env python3
"""Tiny assembler for the protocol-emulator PIO ISA.

The output is a list of 16-bit words suitable for CMD_PROGRAM frames.
"""
from dataclasses import dataclass

OP = {"nop": 0, "movi": 1, "write": 2, "sample": 3, "delay": 4,
      "wait_high": 5, "jmp": 6, "jnz": 7, "halt": 8, "trace": 9}

def assemble(line: str) -> int:
    p = line.lower().replace(",", " ").split()
    if not p or p[0].startswith("#"): return 0
    op = OP[p[0]]
    if p[0] in ("movi", "sample"):
        reg = int(p[1].lstrip("r"), 0)
        imm = int(p[2], 0) if p[0] == "movi" else 0
        return (op << 12) | (reg << 10) | imm
    if p[0] == "jnz": return (op << 12) | (int(p[1].lstrip("r"), 0) << 10) | int(p[2], 0)
    if p[0] in ("delay", "wait_high", "jmp"): return (op << 12) | int(p[1], 0)
    return op << 12

@dataclass
class State:
    pc: int = 0
    pins: int = 0
    oe: int = 0
    regs: list[int] = None
    def __post_init__(self):
        if self.regs is None: self.regs = [0] * 4

def step(state: State, word: int, pin_in: int = 0) -> State:
    """Reference one instruction; used by trace-oriented test harnesses."""
    op, reg, imm = word >> 12, (word >> 10) & 3, word & 0xff
    if op == OP["movi"]: state.regs[reg] = imm
    elif op == OP["sample"]: state.regs[reg] = pin_in
    elif op == OP["write"]: state.pins, state.oe = state.regs[0], state.regs[1]
    elif op == OP["jmp"]: state.pc = imm; return state
    elif op == OP["halt"]: state.oe = 0; return state
    state.pc = (state.pc + 1) & 0xff
    return state
