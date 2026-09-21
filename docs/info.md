<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

The chip is a general-purpose, reprogrammable protocol emulator: two
independent PIO-style engines execute 16-bit microprograms that read pins,
write pins, count clocks and branch with cycle-exact timing, so UART, SPI,
I2C, Manchester/10BASE-T symbol streams and protocols invented after tapeout
are all just programs.

* **Engines.** Each engine has 128 instruction words, four 8-bit registers, a
  carry, a 12-bit delay counter and its own pin output/enable registers.  One
  instruction retires per 40 MHz clock; `DELAY` and `WAIT` stall
  deterministically.  Rotating shift-out writes serialise a byte one bit per
  instruction, shift-in samples deserialise, and waits target level or edge on
  any of the 13 target inputs, the mailbox flags or the other engine.
* **Safe shared pins.** The host grants each engine a drive mask.  If both
  engines enable the same pin in the same clock it is left high impedance and
  a sticky collision fault is latched; a drive outside the mask is dropped and
  latches a permission fault.  Unowned pins are driven by host GPIO registers.
* **Host link.** A synchronous SPI mode-0 slave (32-bit frames, up to clk/8)
  loads programs, starts and stops engines, exchanges mailbox bytes, fills a
  128-byte data FIFO, sets permissions and capture options, and reads back
  status, mailboxes, the timestamp and trace entries on MISO.
* **Data FIFO and CRC-32.** Engines pop FIFO bytes in one clock, so a fast
  protocol can stream a frame loaded ahead of time; a hardware CRC-32 folds
  the bytes in and hands back the Ethernet FCS, or serves other framings
  through explicit instructions.
* **Sub-clock timing.** Two tapped delay lines (128 CMOS5L delay cells
  each) time input edges and place output edges at about 0.2 ns resolution
  from the 40 MHz clock, self-calibrated against the clock period.  A
  program or the host reads the arrival time of an edge, edges can be
  traced with fine timestamps, and a chosen output can be shifted by a
  programmable number of stages.
* **Trace and trigger.** A 32-entry buffer stores timestamped engine events
  and, once armed and triggered (immediately, TRIGGER_IN edge, engine event or
  watched-pin change), every change of the watched pins: a small logic
  analyser for the protocol under test.

The ISA, host protocol and assembler are documented in `docs/isa.md`; the
Python reference model in `tools/proto_ref.py` is cycle-exact and the cocotb
suite compares the RTL against it pin-for-pin on every clock.

## How to test

1. Hold `rst_n` low for a few clocks with `CFG_CS_N` high, then release it.
   Every pin is an input; `uo[1..7]` are low.
2. Send `GPIO` frames (`0x1` in the top nibble) to drive `uio`/`uo` pins
   directly, or read the ID register: frame `0x80000004` then a zero frame
   returns `0x50494F31` on MISO.
3. Assemble a program (`tools/proto_asm.py --frames examples/uart_tx.pio`),
   send its `PROGRAM` frames, then `RUN` with bit 0 set.  `uio[0]` idles high
   and transmits every byte written with a `MBOX` frame at the baud rate set
   by the `.equ BIT` constant.
4. `examples/` also holds a UART receiver, an SPI master, an I2C master, a
   an I2C EEPROM emulator (device side, data from the FIFO), a JTAG
   master (IDCODE read), an SWD host (DPIDR read), a PS/2 device,
   a CAN 2.0A transmitter (bit stuffing, CRC-15, ACK), a low-speed USB
   packet transmitter (NRZI, bit stuffing, CRC-16) with token capture
   through the timestamped trace buffer,
   a 10 Mbit/s Manchester transmitter that sends a complete Ethernet frame
   (preamble, SFD, FIFO payload, hardware FCS) with a matching receiver, a
   10BASE-T link-pulse generator, sub-clock edge timing and glitch
   programs, and a GPIO bring-up blinker.

## External hardware

The Tiny Tapeout demo board's RP2040 is the intended host: three inputs and
one output carry the SPI link.  Protocol targets need only wiring plus
pull-ups for open-drain buses (I2C) and level translation if the target is not
1.2 V-compatible.  The Manchester programs drive a digital symbol on
`TARGET_OUT0` with `TARGET_OUT1` as transmit enable and expect a comparator
output on `TARGET_IN0`; a 10BASE-T experiment therefore needs an external
line driver, receive comparator and magnetics board.  Frames are complete
(preamble, SFD, payload, CRC-32 FCS) but the chip makes no claim of MDI
compliance and does not drive a cable directly.
