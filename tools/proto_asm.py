#!/usr/bin/env python3
"""Assembler and disassembler for the protocol-emulator PIO ISA.

Instruction words are 16 bits: [15:12] opcode, [11:10] rd/rs, [9:8] sub-field,
[7:0] immediate.  See src/proto_pio_engine.sv for the authoritative semantics
and tools/proto_ref.py for the executable reference model.

Syntax (case-insensitive, `#` `;` `//` start comments, labels end with `:`):

    movi  r0, 0x55
    out   r0, UIO, 0xff           ; write masked bits of r0 to a target
    in    r1, UIO_IN              ; UIO_IN | AUX | MBOX | UIO_DATA | TDC0 | TDC1 | TDCLVL
    delay 10                      ; delay r2 | delay 10*16 | delay r2*16
    wait  HIGH, GPIO3             ; LOW | HIGH | RISE | FALL
    jmp   label
    jnz   r0, label               ; djnz | jz | djz
    jph   TRIG, label             ; jpl
    setp  UIO, 3, 1               ; setp UIO_OE, 3, C | NC (carry / inverted carry)
    shout_msb r0, UO, 2           ; rotate r0 left, write its old MSB (INV inverts)
    shout_lsb r0, UIO, 0, INV     ; rotate r0 right, write its old LSB
    shl   r0                      ; shr | shin_lsb r0, GPIO1 | shin_msb r0, GPIO1
    mov   r0, r1                  ; add sub and or xor movc not
    pop   r0                      ; next host FIFO byte, C = valid
    crci | crcu r0 | crcb r1, 2   ; CRC init / fold byte r0 / read check byte 2
    crcbit                        ; fold one bit (the carry) into the CRC
    dtcw  r0, 1                   ; DTC channel 1 delay tap = r0
    trace 0x42 | trace r0 | mbox r0 | done
    halt | nop

    .equ NAME value               ; symbolic constants
    .org 0x20                     ; place following code at an address
    .word 0x1234                  ; raw data word
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field

OPC = {
    "nop": 0x0, "movi": 0x1, "out": 0x2, "in": 0x3, "delay": 0x4, "wait": 0x5,
    "jmp": 0x6, "br": 0x7, "jph": 0x8, "jpl": 0x9, "setp": 0xA, "shift": 0xB,
    "alu": 0xC, "evt": 0xD, "halt": 0xE,
}
TARGETS = {"uio": 0, "uio_oe": 1, "uo": 2, "uo_oe": 3}
IN_SRC = {"uio_in": 0, "aux": 1, "mbox": 2, "uio_data": 3}
IN3_SUB = {"uio_data": 0, "tdc0": 1, "tdc1": 2, "tdclvl": 3}
WAIT_COND = {"low": 0, "high": 1, "rise": 2, "fall": 3}
BR_COND = {"jnz": 0, "djnz": 1, "jz": 2, "djz": 3}
SHIFT_MODE = {"shl": 0, "shr": 1, "shin_lsb": 2, "shin_msb": 3}
ALU_FN = {"mov": 0, "add": 1, "sub": 2, "and": 3, "or": 4, "xor": 5, "movc": 6, "not": 7,
          "crcu": 8, "crci": 9, "crcb": 10, "pop": 11, "dtcw": 12, "crcbit": 13}
EVT_SUB = {"trace": 0, "mbox": 2, "done": 3}
PINS = {**{f"gpio{i}": i for i in range(8)}, **{f"in{i}": 8 + i for i in range(4)},
        "trig": 12, "mbox_in": 13, "mbox_out": 14, "peer": 15}


class AsmError(Exception):
    pass


@dataclass
class Program:
    words: list[int] = field(default_factory=list)
    labels: dict[str, int] = field(default_factory=dict)
    listing: list[tuple[int, int, str]] = field(default_factory=list)


def _reg(tok: str) -> int:
    m = re.fullmatch(r"r([0-3])", tok)
    if not m:
        raise AsmError(f"expected register r0-r3, got {tok!r}")
    return int(m.group(1))


def _value(tok: str, symbols: dict[str, int], bits: int = 8) -> int:
    tok = tok.strip()
    if tok in symbols:
        v = symbols[tok]
    else:
        try:
            v = int(tok, 0)
        except ValueError as e:
            raise AsmError(f"unknown symbol or number {tok!r}") from e
    if not 0 <= v < (1 << bits):
        raise AsmError(f"value {v} does not fit in {bits} bits")
    return v


def _pin(tok: str, symbols: dict[str, int]) -> int:
    return PINS[tok] if tok in PINS else _value(tok, symbols, 4)


def _tokens(line: str) -> list[str]:
    line = re.split(r"#|;|//", line, maxsplit=1)[0].strip()
    if not line:
        return []
    return [t for t in re.split(r"[\s,]+", line) if t]


def encode(mnemonic: str, args: list[str], symbols: dict[str, int]) -> int:
    m = mnemonic.lower()
    a = [x.lower() for x in args]
    n = len(a)
    if m == "nop":
        return OPC["nop"] << 12
    if m == "halt":
        return OPC["halt"] << 12
    if m == "movi" and n == 2:
        return (OPC["movi"] << 12) | (_reg(a[0]) << 10) | _value(a[1], symbols)
    if m == "out" and n in (2, 3):
        mask = _value(a[2], symbols) if n == 3 else 0xFF
        return (OPC["out"] << 12) | (_reg(a[0]) << 10) | (TARGETS[a[1]] << 8) | mask
    if m == "in" and n == 2:
        if a[1] in IN3_SUB:
            return (OPC["in"] << 12) | (_reg(a[0]) << 10) | (3 << 8) | IN3_SUB[a[1]]
        return (OPC["in"] << 12) | (_reg(a[0]) << 10) | (IN_SRC[a[1]] << 8)
    if m == "delay" and n == 1:
        tok = a[0]
        scale = 0
        if tok.endswith("*16"):
            tok, scale = tok[:-3], 2
        if re.fullmatch(r"r[0-3]", tok):
            return (OPC["delay"] << 12) | (_reg(tok) << 10) | ((scale | 1) << 8)
        return (OPC["delay"] << 12) | (scale << 8) | _value(tok, symbols)
    if m == "wait" and n == 2:
        return (OPC["wait"] << 12) | (WAIT_COND[a[0]] << 10) | _pin(a[1], symbols)
    if m == "jmp" and n == 1:
        return (OPC["jmp"] << 12) | _value(a[0], symbols)
    if m in BR_COND and n == 2:
        return (OPC["br"] << 12) | (_reg(a[0]) << 10) | (BR_COND[m] << 8) | _value(a[1], symbols)
    if m in ("jph", "jpl") and n == 2:
        return (OPC[m] << 12) | (_pin(a[0], symbols) << 8) | _value(a[1], symbols)
    if m == "setp" and n == 3:
        bit = _value(a[1], symbols, 3)
        if a[2] in ("c", "nc"):
            imm = 0x20 | bit | (0x80 if a[2] == "nc" else 0)
        else:
            imm = (_value(a[2], symbols, 1) << 7) | bit
        return (OPC["setp"] << 12) | (TARGETS[a[0]] << 8) | imm
    if m in ("shout_msb", "shout_lsb") and n in (3, 4):
        src = 2 if m == "shout_msb" else 3
        inv = 0x80 if n == 4 and a[3] == "inv" else 0
        return ((OPC["setp"] << 12) | (_reg(a[0]) << 10) | (TARGETS[a[1]] << 8)
                | inv | (src << 5) | _value(a[2], symbols, 3))
    if m in SHIFT_MODE:
        pin = _pin(a[1], symbols) if n == 2 else 0
        return (OPC["shift"] << 12) | (_reg(a[0]) << 10) | (SHIFT_MODE[m] << 8) | pin
    if m in ALU_FN:
        if m in ("crci", "crcbit"):
            return (OPC["alu"] << 12) | ALU_FN[m]
        rd = _reg(a[0])
        if m == "crcb":
            return (OPC["alu"] << 12) | (rd << 10) | (_value(a[1], symbols, 2) << 4) | ALU_FN[m]
        if m == "dtcw":
            return (OPC["alu"] << 12) | (rd << 10) | (_value(a[1], symbols, 1) << 4) | ALU_FN[m]
        rs = _reg(a[1]) if n == 2 else rd
        return (OPC["alu"] << 12) | (rd << 10) | (rs << 8) | ALU_FN[m]
    if m == "trace" and n == 1:
        if re.fullmatch(r"r[0-3]", a[0]):
            return (OPC["evt"] << 12) | (_reg(a[0]) << 10) | (1 << 8)
        return (OPC["evt"] << 12) | _value(a[0], symbols)
    if m == "mbox" and n == 1:
        return (OPC["evt"] << 12) | (_reg(a[0]) << 10) | (2 << 8)
    if m == "done" and n == 0:
        return (OPC["evt"] << 12) | (3 << 8)
    raise AsmError(f"bad instruction: {mnemonic} {' '.join(args)}")


def assemble_program(text: str) -> Program:
    """Two-pass assembly of a whole source file."""
    symbols: dict[str, int] = {}
    items: list[tuple[int, str, list[str], int, str]] = []  # (addr, mnem, args, lineno, raw)
    prog = Program()
    pc = 0
    for lineno, raw in enumerate(text.splitlines(), 1):
        toks = _tokens(raw)
        while toks and toks[0].endswith(":"):
            label = toks.pop(0)[:-1].lower()
            if label in prog.labels or label in symbols:
                raise AsmError(f"line {lineno}: duplicate label {label}")
            prog.labels[label] = pc
        if not toks:
            continue
        head = toks[0].lower()
        if head == ".equ":
            symbols[toks[1].lower()] = int(toks[2], 0)
        elif head == ".org":
            pc = int(toks[1], 0)
        elif head == ".word":
            items.append((pc, ".word", toks[1:], lineno, raw.strip()))
            pc += 1
        else:
            items.append((pc, head, toks[1:], lineno, raw.strip()))
            pc += 1
        if pc > 256:
            raise AsmError(f"line {lineno}: program exceeds 256 words")
    symbols.update(prog.labels)
    size = max((addr for addr, *_ in items), default=-1) + 1
    prog.words = [0] * size
    for addr, mnem, args, lineno, raw in items:
        try:
            word = _value(args[0], symbols, 16) if mnem == ".word" else encode(mnem, args, symbols)
        except (AsmError, KeyError, IndexError) as e:
            raise AsmError(f"line {lineno}: {e}: {raw}") from e
        prog.words[addr] = word
        prog.listing.append((addr, word, raw))
    return prog


def assemble(line: str) -> int:
    """Assemble one instruction with no symbols (convenience for tests)."""
    toks = _tokens(line)
    return encode(toks[0], toks[1:], {}) if toks else 0


def disassemble(word: int) -> str:
    op, rd, sub, imm = word >> 12, (word >> 10) & 3, (word >> 8) & 3, word & 0xFF
    tgt = {v: k.upper() for k, v in TARGETS.items()}
    pin = {v: k.upper() for k, v in PINS.items()}
    if op == 0x0: return "nop"
    if op == 0x1: return f"movi r{rd}, 0x{imm:02x}"
    if op == 0x2: return f"out r{rd}, {tgt[sub]}, 0x{imm:02x}"
    if op == 0x3:
        if sub == 3:
            return f"in r{rd}, {['UIO_DATA','TDC0','TDC1','TDCLVL'][imm & 3]}"
        return f"in r{rd}, {['UIO_IN','AUX','MBOX'][sub]}"
    if op == 0x4:
        base = f"r{rd}" if sub & 1 else f"{imm}"
        return f"delay {base}{'*16' if sub & 2 else ''}"
    if op == 0x5: return f"wait {['LOW','HIGH','RISE','FALL'][rd]}, {pin[imm & 15]}"
    if op == 0x6: return f"jmp 0x{imm:02x}"
    if op == 0x7: return f"{['jnz','djnz','jz','djz'][sub]} r{rd}, 0x{imm:02x}"
    if op in (0x8, 0x9): return f"{'jph' if op == 8 else 'jpl'} {pin[(word >> 8) & 15]}, 0x{imm:02x}"
    if op == 0xA:
        src = (imm >> 5) & 3
        if src >= 2:
            inv = ", INV" if imm & 0x80 else ""
            return f"{'shout_msb' if src == 2 else 'shout_lsb'} r{rd}, {tgt[sub]}, {imm & 7}{inv}"
        if src == 1:
            return f"setp {tgt[sub]}, {imm & 7}, {'NC' if imm & 0x80 else 'C'}"
        return f"setp {tgt[sub]}, {imm & 7}, {imm >> 7}"
    if op == 0xB:
        name = ['shl', 'shr', 'shin_lsb', 'shin_msb'][sub]
        return f"{name} r{rd}" + (f", {pin[imm & 15]}" if sub >= 2 else "")
    if op == 0xC:
        names = ['mov', 'add', 'sub', 'and', 'or', 'xor', 'movc', 'not', 'crcu', 'crci', 'crcb', 'pop', 'dtcw', 'crcbit']
        if (imm & 15) >= len(names):
            return f".word 0x{word:04x}  ; illegal ALU fn"
        fn = names[imm & 15]
        if fn in ('crci', 'crcbit'): return fn
        if fn == 'crcb': return f"crcb r{rd}, {(imm >> 4) & 3}"
        if fn == 'dtcw': return f"dtcw r{rd}, {(imm >> 4) & 1}"
        return f"{fn} r{rd}" + (f", r{sub}" if fn in ('mov', 'add', 'sub', 'and', 'or', 'xor') else "")
    if op == 0xD:
        return [f"trace 0x{imm:02x}", f"trace r{rd}", f"mbox r{rd}", "done"][sub]
    if op == 0xE: return "halt"
    return f".word 0x{word:04x}  ; illegal"


def program_frames(words: list[int], engine: int, base: int = 0) -> list[int]:
    """CMD_PROGRAM frames that load `words` into `engine` starting at `base`."""
    return [(0x2 << 28) | (engine << 27) | ((base + i) << 19) | (w << 3)
            for i, w in enumerate(words)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("source")
    ap.add_argument("-e", "--engine", type=int, default=0, help="engine for --frames")
    ap.add_argument("--frames", action="store_true", help="print CMD_PROGRAM host frames")
    ap.add_argument("-l", "--listing", action="store_true")
    args = ap.parse_args(argv)
    try:
        prog = assemble_program(open(args.source).read())
    except AsmError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.listing:
        for addr, word, raw in prog.listing:
            print(f"{addr:02x}: {word:04x}  {disassemble(word):<28} ; {raw}")
    elif args.frames:
        for f in program_frames(prog.words, args.engine):
            print(f"{f:08x}")
    else:
        for w in prog.words:
            print(f"{w:04x}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
