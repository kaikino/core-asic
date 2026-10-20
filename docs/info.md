<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

Load a microprogram and the same silicon speaks UART, SPI, I2C, JTAG, SWD,
PS/2, CAN, low-speed USB or 10 Mbit/s Manchester Ethernet, as the master
that probes a device or as the device a system under test talks to.  Two
independent PIO-style engines execute 16-bit instructions that read pins,
write pins, count clocks, shift bits and branch, one instruction per 40 MHz
clock, so a program's timing is a property of the program.  Two tapped
delay lines then let it see and place edges *between* clocks, at about
0.2 ns.

* **Engines.** Each engine has 128 instruction words, four 8-bit registers,
  a carry, a 12-bit delay counter and its own pin output and enable
  registers.  `DELAY` and `WAIT` stall deterministically.  Rotating shift-out
  writes serialise a byte one bit per instruction, shift-in samples
  deserialise, and waits target a level or edge on any of the 13 target
  inputs, the mailbox flags or the other engine.
* **Safe shared pins, proved.** The host grants each engine a drive mask.
  If both engines enable the same pin in the same clock it is left high
  impedance and a sticky collision fault is latched; a drive outside the
  mask is dropped and latches a permission fault.  Unowned pins are driven
  by host GPIO registers.  SymbiYosys proves this and ten further safety
  properties for all inputs.
* **Host link.** A synchronous SPI mode-0 slave (32-bit frames, SCK up to
  5 MHz) loads programs, starts and stops engines, exchanges mailbox bytes,
  fills the data FIFO, sets permissions and capture options, and reads back
  status, mailboxes, the timestamp and trace entries on MISO.  It is sampled
  by the core clock, so the whole chip is one clock domain.
* **Data FIFO and programmable CRC.** Engines pop bytes from a 128-byte FIFO
  in one clock, so a fast protocol can stream a frame loaded ahead of time.
  A CRC unit with any reflected polynomial, folded bit- or byte-wise, hands
  back an Ethernet CRC-32 FCS, a USB CRC-16 or a CAN CRC-15.
* **Sub-clock timing.** Two tapped delay lines (128 CMOS5L delay cells each)
  feed two time-to-digital and two digital-to-time channels: a program or
  the host reads the arrival time of an input edge in stages of about
  0.22 ns, traced edges carry fine timestamps, and a chosen output can be
  shifted by a programmable number of stages.  The stage delay is
  calibrated against the clock period on the chip itself.
* **Trace and trigger.** A 32-entry buffer stores timestamped engine events
  and, once armed and triggered (immediately, on a TRIGGER_IN edge, an
  engine event or a watched-pin change), every change of the watched pins:
  a small logic analyser for the protocol under test.

The ISA, host protocol and assembler are documented in `docs/isa.md`.  The
Python reference model in `tools/proto_ref.py` is cycle-exact, delay lines
included, and the cocotb suite compares the RTL against it pin-for-pin on
every clock; the same 31 tests pass on the RTL, the FPGA netlist and the
final gate-level netlist.

## How to test

1. Hold `rst_n` low for a few clocks with `CFG_CS_N` high, then release it.
   Every pin is an input; `uo[1..7]` are low.
2. Send `GPIO` frames (`0x1` in the top nibble) to drive `uio`/`uo` pins
   directly, or read the ID register: frame `0x80000004` then a zero frame
   returns `0x50494F31` (`PIO1`) on MISO.
3. Assemble a program (`tools/proto_asm.py --frames examples/uart_tx.pio`),
   send its `PROGRAM` frames, then `RUN` with bit 0 set.  `uio[0]` idles high
   and transmits every byte written with a `MBOX` frame at the baud rate set
   by the `.equ BIT` constant.
4. `examples/` holds twenty programs to load the same way:

| Program | What it does |
|---|---|
| `uart_tx.pio`, `uart_rx.pio` | 8N1 transmitter and receiver |
| `spi_master.pio`, `spi_flash.pio` | SPI mode 0 master; SPI flash emulator serving READ ID and READ from the FIFO |
| `i2c_master.pio`, `i2c_eeprom.pio`, `i2c_watch.pio` | I2C master with clock stretching; EEPROM emulator; address-match trigger for the analyser |
| `jtag_idcode.pio`, `swd_dpidr.pio` | JTAG IDCODE read; SWD DPIDR read including the JTAG-to-SWD switch |
| `ps2_device.pio` | PS/2 device frames with parity |
| `can_tx.pio` | CAN 2.0A frame with bit stuffing, CRC-15 and ACK |
| `usb_ls_tx.pio`, `usb_ls_rx_in.pio`, `usb_ls_device.pio` | Low-speed USB DATA packet (NRZI, stuffing, CRC-16); a two-engine device that answers IN tokens with DATA0 |
| `manchester_tx.pio`, `manchester_rx.pio`, `link_pulse.pio` | 10 Mbit/s Manchester Ethernet frame with hardware FCS; receiver; 10BASE-T link pulses |
| `edge_timer.pio`, `glitch_pulse.pio` | Sub-clock edge timing into the mailbox; a pulse placed to the delay stage after a trigger |
| `gpio_blink.pio` | Bring-up blinker |

`tools/tt_board_host.py` is a MicroPython driver for the demo board's
RP2040 that sends the frames, and `tools/board_demo.py` brings a die up:
ID readback, delay-line self-calibration, host GPIO drive, then a UART hello
at 2 Mbaud.

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
