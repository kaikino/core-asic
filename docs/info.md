<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

This project is a programmable, general-purpose protocol emulator. Two
deterministic PIO engines execute 16-bit programs to sample and drive GPIO
pins with cycle-level timing. Programs arrive as 32-bit MSB-first frames on
`CFG_SCK`, `CFG_MOSI`, and active-low `CFG_CS_N`; `CFG_MISO` exposes status.

Command `0x2` writes `{engine, address, instruction}` while that engine is
stopped; command `0x3` starts/stops engines. The ISA, assembler, and a UART
example are in `tools/proto_asm.py` and `examples/uart_tx.pio`.

Both engines may read any GPIO. If they concurrently request push-pull drive
of the same GPIO, the pin becomes high impedance and a sticky collision flag
is recorded.

## How to test

Hold reset low, release it, load instructions, then start an engine. `uo[7]`
becomes high after the first clock. `uio` pins remain inputs unless a running
PIO program explicitly enables them.

## External hardware

The final design will use the Tiny Tapeout dev-board controller as a serial
programmer. Protocol targets may require external pull-ups, level translation,
or an Ethernet line-interface/magnetics daughterboard.
