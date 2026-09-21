# Submission: a protocol emulator that treats time as data

*Jane Street protocol-emulator ASIC challenge, Tiny Tapeout IHP CMOS5L,
8x4 tiles, 40 MHz.  Repository layout, build and test instructions are in
the README; this document is the case for the design.*

## What is different about it

Every PIO-style engine, including the RP2040's and this one's, acts on
clock edges.  This chip adds two tapped delay lines built from 128 CMOS5L
delay cells each, so a program can **measure when an input edge arrived and
place an output edge** at about a fifth of a nanosecond, from a 40 MHz
clock, and calibrate the stage delay against the clock itself.  A
protocol emulator that can also report the setup margin it is seeing on a
live bus, fire a fault-injection pulse to the nanosecond, or timestamp
edges a hundred times finer than its clock is a different kind of tool
from one that only bit-bangs.  Section 13a of `docs/system-design.md`
explains the circuit; `experiments/tdc/README.md` records the measurement:
0.15 / 0.22 / 0.35 ns per stage at the fast / typical / slow corner,
every cell intact after place-and-route, under 1 % of the tile.

Three further choices set the architecture apart from a PIO clone:

* **Proven-safe pin sharing.** Two engines and the host share the pins
  under host-set permission masks; a pin both engines enable in the same
  clock goes high impedance and latches a fault.  That rule is not just
  tested, it is proved for all inputs by SymbiYosys, along with eight
  other safety properties.
* **A data path fast enough for line-rate protocols.** A 128-byte host
  FIFO popped in one clock and a programmable CRC unit (any reflected
  polynomial, bit- or byte-wise) let a microprogram stream a complete
  Ethernet frame with a hardware FCS at 10 Mbit/s, or a CAN frame with
  CRC-15, or a USB packet with CRC-16, from 128 words of program memory.
* **One clock domain.** The SPI host link is oversampled by the core
  clock instead of clocked by SCK, so timing sign-off, the formal model
  and the gate-level simulation all see a single synchronous design.

## What it does, from microcode

| Protocol | Program | Checked by |
|---|---|---|
| UART 8N1 TX and RX | `uart_tx.pio`, `uart_rx.pio` | independent Python monitor and source |
| SPI mode 0 master | `spi_master.pio` | Python slave, both directions |
| I2C master, clock stretching | `i2c_master.pio` | ACKing Python slave |
| I2C slave: EEPROM emulation from the FIFO | `i2c_eeprom.pio` | scripted Python master, repeated start, ACK/NACK |
| SPI slave: flash emulation (READ ID, READ from the FIFO) | `spi_flash.pio` | scripted Python master |
| JTAG IDCODE read | `jtag_idcode.pio` | 16-state TAP model |
| SWD DPIDR read incl. JTAG-to-SWD switch | `swd_dpidr.pio` | SWD target model |
| PS/2 device frames | `ps2_device.pio` | host monitor with parity |
| CAN 2.0A frame: stuffing, CRC-15, ACK | `can_tx.pio` | destuffing monitor with CRC and ACK |
| Low-speed USB DATA packet: NRZI, stuffing, CRC-16 | `usb_ls_tx.pio` | NRZI decoder |
| Low-speed USB token capture | trace buffer | edge-timestamp decoder, CRC-5 |
| Protocol-aware trigger: I2C address match starts the logic analyser | `i2c_watch.pio` | scripted master, captured edges all follow the match |
| UART auto-baud from captured edge timestamps | trace buffer | infers a 37-clock bit period from 14 edges |
| 10 Mbit/s Manchester Ethernet frame with FCS, and receiver | `manchester_tx.pio`, `manchester_rx.pio` | software decode and the second engine |
| 10BASE-T link pulses | `link_pulse.pio` | |
| Sub-clock edge timing and glitch placement | `edge_timer.pio`, `glitch_pulse.pio` | edges at 1/6/12/20 ns after the clock read 107/84/58/22 stages, exactly as modelled; the glitch appears at its tap |

Every program on the challenge's list (UART, SPI, I2C, both stretch goals,
JTAG, SWD, PS/2, CAN) runs from loaded microcode on the same silicon, and
the chip can sit on either side of a bus: as the master that probes a
device, or as the device (I2C EEPROM, SPI flash, PS/2 keyboard, USB
packet source) that a system under investigation talks to.

## How it is verified

The brief asks for verification as much as design, and this is where the
project spent the most effort.

* **An executable specification.** `tools/proto_ref.py` is a cycle-exact
  Python model of the whole chip, including the sub-clock timing.  The
  cocotb harness keeps it in lock-step with the RTL and compares every
  target pin, the timestamp and the delay-line fine times on every clock,
  then the status word and the whole trace buffer at the end of each test.
  The model found two RTL bugs before the first commit (a branch decoding
  the wrong field; a trace timestamp off by one) and every later bug in
  a test or peer model was localised by the first mismatching clock.
* **Directed protocol tests** with peers that know nothing of the RTL
  (table above), **constrained-random** programs on both engines with
  random pads, masks, FIFO and timing traffic, and **formal proof** of
  nine safety properties by k-induction plus bounded model checking.
* **The same suite on three netlists**: RTL, the iCE40 FPGA netlist, and
  the final gate-level netlist from the ASIC flow.  What is taped out is
  what was tested.
* **AI-assisted.** The design, model, tests and flow were built with an
  AI coding assistant; the model-lock-step method is what made that safe,
  because every generated change had to agree with an independent
  executable specification on every clock.

## Sign-off

See `docs/signoff.md` for the record.  In one line: 8x4 CMOS5L at
40 MHz, ~67 % utilisation, zero routing DRC, Magic DRC, LVS and antenna
violations, setup and hold met at every corner, all 512 delay cells
intact under a custom SDC, Tiny Tapeout precheck clean, RTL / FPGA /
gate-level suites 27/27.

## Limits, stated plainly

* Program memory is 128 words per engine (no SRAM macro on this PDK);
  every example fits, the largest at 118 words.
* Ethernet and USB are demonstrated as packet/frame generation and
  capture with real framing and checksums; the chip is not a MAC or a
  USB device controller and does not drive a cable directly.
* The delay lines span a full period at the typical and slow corners and
  about 80 % of one at the fast corner; the calibration reference is half
  a period so it never saturates.
* The delay-line behaviour is modelled (224 ps per stage) in simulation;
  the real stage delay is read from the calibration source on silicon.
