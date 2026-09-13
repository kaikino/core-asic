# Programmable Protocol Emulator ASIC

An open-source, reprogrammable GPIO protocol emulator for Tiny Tapeout on
IHP's CMOS5L (130 nm) process, built for the Jane Street protocol-emulator
challenge.  Two deterministic PIO engines execute 16-bit microprograms with
cycle-exact timing, share the pins safely, and stream events into a
timestamped trace buffer.  UART, SPI, I2C and 10 Mbit/s Manchester are
microprograms in `examples/`, not fixed blocks.

| | |
|---|---|
| Process / flow | IHP SG13CMOS5L, Tiny Tapeout LibreLane flow (`tt-gds-action@ihp-cmos5l`) |
| Tile allocation | 8x4 (`info.yaml`); local sign-off at 8x4 with `flow/harden.sh` |
| Clock | 40 MHz (25 ns) |
| Engines | 2 x (128 x 16 program words, 4 registers, carry, 12-bit delay) |
| Pins | 8 bidirectional GPIO, 4 sample-only inputs, 7 drive-only outputs, 4 host-link pins |
| Trace | 32 entries x 32 bits, 16-bit timestamps, trigger + pin-change capture |
| Host link | synchronous SPI mode 0, 32-bit frames, register readback on MISO |
| Sign-off (8x4, 40 MHz) | setup slack +8.2 ns slow corner, hold met, 0 DRC/LVS/antenna, 53 % utilisation; see `docs/signoff.md` |

## Layout

```
src/        RTL (SystemVerilog): top level, PIO engine, host link, memories
tools/      proto_asm.py (assembler/disassembler), proto_ref.py (cycle-accurate model)
examples/   microprograms: uart_tx/rx, spi_master, i2c_master, manchester_tx/rx, link_pulse
test/       cocotb suite (lock-step RTL vs model), Verilog smoke tests, pytest unit tests
formal/     SymbiYosys safety properties (k-induction proof and BMC)
flow/       PDK install, local hardening, 8x4 tile-template generator
docs/       info.md (datasheet), isa.md (ISA + host protocol), signoff.md (results)
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
  40 MHz clock that is enough for two instructions per Manchester half bit.
* A pin enabled by both engines in the same clock is left high impedance and
  a sticky fault is latched; host-set permission masks bound each engine.
* The CMOS5L slim PDK ships no SRAM macro, so program memories are flop
  arrays behind an SRAM-shaped wrapper (`src/proto_program_ram.sv`).

See `docs/isa.md` for the instruction set and host protocol.
