# Programmable Protocol Emulator ASIC

An open-source, reprogrammable GPIO protocol emulator for Tiny Tapeout on
IHP's CMOS5L (130 nm) process, built for the Jane Street protocol-emulator
challenge.  Two deterministic PIO engines execute 16-bit microprograms with
cycle-exact timing, share the pins safely, and stream events into a
timestamped trace buffer.  UART, SPI, I2C and 10 Mbit/s Manchester Ethernet
frames (with hardware CRC-32) are microprograms in `examples/`, not fixed
blocks.

| | |
|---|---|
| Process / flow | IHP SG13CMOS5L, Tiny Tapeout LibreLane flow (`tt-gds-action@ihp-cmos5l`) |
| Tile allocation | 8x4 (`info.yaml`); local sign-off at 8x4 with `flow/harden.sh` |
| Clock | 40 MHz (25 ns) |
| Engines | 2 x (128 x 16 program words, 4 registers, carry, 12-bit delay) |
| Pins | 8 bidirectional GPIO, 4 sample-only inputs, 7 drive-only outputs, 4 host-link pins |
| Trace | 32 entries x 32 bits, 16-bit timestamps, trigger + pin-change capture |
| Data path | 128-byte host FIFO, one-clock pops, hardware CRC-32 (Ethernet FCS) |
| Sub-clock timing | 2 TDC + 2 DTC channels on 128-stage delay lines: ~0.22 ns edge timing and placement from a 40 MHz clock, self-calibrating |
| Host link | synchronous SPI mode 0, 32-bit frames, register readback on MISO |
| Sign-off (8x4, 40 MHz) | setup slack +8.2 ns slow corner, hold met, 0 DRC/LVS/antenna, 53 % utilisation; see `docs/signoff.md` |

## Layout

```
src/        RTL (SystemVerilog): top level, PIO engine, host link, memories
tools/      proto_asm.py (assembler/disassembler), proto_ref.py (cycle-accurate model)
examples/   microprograms: uart, spi, i2c, jtag, swd, ps2, manchester (Ethernet), link_pulse, edge_timer, glitch_pulse
test/       cocotb suite (lock-step RTL vs model), Verilog smoke tests, pytest unit tests
formal/     SymbiYosys safety properties (k-induction proof and BMC)
flow/       PDK install, local hardening, 8x4 tile-template generator
docs/       system-design.md (guided tour), info.md (datasheet), isa.md (ISA + host protocol), signoff.md (results)
```

## Quick start

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r test/requirements.txt
make -C test            # cocotb suite (iverilog)
make -C test smoke      # plain Verilog smoke tests
python -m pytest test/test_assembler.py
sby -f formal/proto.sby # formal proof (yosys + sby + z3)
python tools/proto_asm.py -l examples/uart_tx.pio   # listing
```

Hardening locally (Docker, LibreLane 3.0.0rc1) is described in
`docs/signoff.md`.

## Design notes

* Every instruction takes exactly one clock; `DELAY` and `WAIT` stall
  deterministically, so the protocol timing is a property of the program.
* `shout_*` rotates a register and writes one bit per instruction; with a
  40 MHz clock that is enough for two instructions per Manchester half bit,
  and `pop` fetches the next frame byte from the host FIFO in one clock.
* A pin enabled by both engines in the same clock is left high impedance and
  a sticky fault is latched; host-set permission masks bound each engine.
* Two tapped delay lines time input edges and place output edges at about a
  tenth of a nanosecond, calibrated against the clock itself, so the chip can
  measure setup and hold on a live bus or fire a glitch at a chosen offset.
* The CMOS5L slim PDK ships no SRAM macro, so program memories are flop
  arrays behind an SRAM-shaped wrapper (`src/proto_program_ram.sv`).

New to the project? Start with `docs/system-design.md`, a piece-by-piece
walkthrough for readers who know basic SystemVerilog but have never designed
a chip. `docs/isa.md` has the instruction set and host protocol reference.
