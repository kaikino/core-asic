# Programmable protocol emulator: ISA and host interface

## Pins

| Pin | Name | Direction | Role |
|-----|------|-----------|------|
| ui[0] | CFG_SCK | in | host link SPI clock (mode 0, at most clk/8) |
| ui[1] | CFG_MOSI | in | host link data in |
| ui[2] | CFG_CS_N | in | host link chip select, active low |
| ui[3..6] | TARGET_IN0..3 | in | sample-only target inputs (engine pins 8..11) |
| ui[7] | TRIGGER_IN | in | capture trigger, also readable as engine pin 12 |
| uo[0] | CFG_MISO | out | host link data out |
| uo[1..7] | TARGET_OUT0..6 | out | drive-only target outputs (`UO` target bits 0..6) |
| uio[0..7] | GPIO0..7 | bidir | bidirectional target pins (`UIO` target, engine pins 0..7) |

All 13 target inputs pass through two synchroniser flops and one history flop,
so an engine observes a pad change two clocks after it happens and edge waits
compare consecutive synchronised samples.

## Engine model

Each of the two engines has:

* `pc` (8 bit; program memory holds 128 words, bit 7 is ignored),
* four 8-bit registers `r0..r3`, a carry flag `C`, a 12-bit delay counter,
* output registers `UIO` data (8), `UIO_OE` (8), `UO` data (7), `UO_OE` (7),
* `running` and `done` flags.

Every instruction retires in exactly one clock.  `DELAY n` stalls the engine
for `n` further clocks and `WAIT` stalls until its condition holds, so the
cycle count of any straight-line sequence is a pure function of the program
and the sampled inputs.  Pin writes take effect on the pad at the clock edge
that retires the instruction.

Pin numbers used by `WAIT`, `JPH`, `JPL` and `SHIN`:

| # | Name | # | Name |
|---|------|---|------|
| 0..7 | GPIO0..7 | 12 | TRIG |
| 8..11 | IN0..3 | 13 | MBOX_IN (host wrote a byte, not yet read) |
| | | 14 | MBOX_OUT (byte to host not yet acknowledged) |
| | | 15 | PEER (other engine running) |

`IN rd, AUX` reads `{PEER, MBOX_OUT, MBOX_IN, TRIG, IN3..IN0}` into `rd`.

## Instruction encoding

`[15:12]` opcode, `[11:10]` register `rd`/`rs`, `[9:8]` sub-field, `[7:0]` immediate.

| Op | Mnemonic | Semantics |
|----|----------|-----------|
| 0 | `nop` | |
| 1 | `movi rd, imm8` | `rd = imm` |
| 2 | `out rs, TGT[, mask8]` | `TGT = (TGT & ~mask) \| (rs & mask)`; TGT is `UIO`, `UIO_OE`, `UO`, `UO_OE` |
| 3 | `in rd, SRC` | SRC: `UIO_IN` (pins 7..0), `AUX`, `MBOX` (pops the host byte), `UIO_DATA` (readback), `TDC0` / `TDC1` (fine time of the channel's last edge, in delay-line stages), `TDCLVL` (`{TDC1 level, TDC0 level}`) |
| 4 | `delay imm8` / `delay rs` / `delay imm8*16` / `delay rs*16` | stall that many extra clocks |
| 5 | `wait COND, pin` | COND `LOW`, `HIGH`, `RISE`, `FALL`; stall until true |
| 6 | `jmp addr8` | |
| 7 | `jnz rd, a` / `djnz rd, a` / `jz rd, a` / `djz rd, a` | branch on register (`d*` decrements first) |
| 8 | `jph pin, a` | branch if pin high |
| 9 | `jpl pin, a` | branch if pin low |
| A | `setp TGT, bit, 0\|1\|C\|NC` | write one bit of a target from an immediate or the carry |
| A | `shout_msb rs, TGT, bit[, INV]` | rotate `rs` left, write its old MSB (inverted with `INV`); `C` = that bit |
| A | `shout_lsb rs, TGT, bit[, INV]` | rotate `rs` right, write its old LSB |
| B | `shl rd` / `shr rd` | shift, `C` = bit shifted out |
| B | `shin_lsb rd, pin` / `shin_msb rd, pin` | shift the sampled pin into the MSB (LSB-first receive) or LSB |
| C | `mov/add/sub/and/or/xor rd, rs` | ALU (`fn = imm[3:0]`); `add`/`sub` set `C` (carry / borrow) |
| C | `movc rd` / `not rd` | `rd = C` / `rd = ~rd` |
| C | `pop rd` | `rd` = next byte of the host data FIFO, `C` = byte was valid; in FCS mode the four bytes after the data are the CRC-32 FCS |
| C | `crci` / `crcu rd` / `crcb rd, k` | CRC: initialise / fold byte `rd` in (LSB first) / `rd` = check value byte `k` (0 = first on the wire) |
| C | `crcbit` | fold one bit, the carry, into the CRC (for bit-oriented framings such as CAN and USB tokens) |
| C | `dtcw rd, ch` | DTC channel `ch` delay tap = `rd` stages |
| D | `trace imm8` / `trace rs` | write a timestamped event to the trace buffer |
| D | `mbox rs` | post a byte to the host mailbox |
| D | `done` | set the engine's DONE status flag |
| E | `halt` | stop and release every output enable |
| F | (illegal) | halt, release outputs, latch the illegal-opcode fault |

Assembler syntax (`tools/proto_asm.py`): labels end with `:`, `.equ NAME value`
defines a constant, `.org addr` and `.word value` are supported, comments start
with `;`, `#` or `//`.  `proto_asm.py --frames -e 1 prog.pio` prints the host
frames that load the program into engine 1.

### Timing recipes

* UART bit = `shout_lsb` (1) + `delay BIT` (1 + BIT) + `djnz` (1) = `BIT + 3` clocks.
* SPI half period = `setp` (1) + `delay HALF` (1 + HALF) = `HALF + 2` clocks.
* Manchester at 40 MHz: two instructions per half bit; see `examples/manchester_tx.pio`.

## Host link

SPI mode 0 slave, 32-bit frames, MSB first, at most one eighth of the core
clock (5 MHz at 40 MHz).  Every frame is a command `{op[3:0], arg[27:0]}` and
simultaneously shifts out the readback register selected by earlier frames;
that value is captured while `CFG_CS_N` is high, so read with two frames:
`READSEL n`, then any frame (usually `NOP`) whose response carries register n.

| Op | Name | Argument |
|----|------|----------|
| 0 | NOP | |
| 1 | GPIO | `[7:0]` uio_oe, `[15:8]` uio_data, `[22:16]` uo_data — host drive of pins no engine owns |
| 2 | PROGRAM | `[27]` engine, `[26:19]` address, `[18:3]` instruction; ignored while the engine runs |
| 3 | RUN | `[0]` start0 `[1]` stop0 `[2]` start1 `[3]` stop1 `[4]` clear faults `[5]` reset timestamp `[6]` arm capture (clears the buffer) `[7]` disarm `[8]` ack mailbox0 `[9]` ack mailbox1 `[10]` clear DONE flags `[23:16]` start pc |
| 4 | TRACEPTR | `[4:0]` trace entry to read back |
| 5 | PERM | `[27]` engine, `[7:0]` GPIO drive mask, `[14:8]` TARGET_OUT drive mask (reset: all) |
| 6 | MBOX | `[27]` engine, `[7:0]` byte for the engine |
| 7 | CAPTURE | `[12:0]` watched pins (bit 12 = TRIGGER_IN), `[15:13]` trigger source, `[16]` capture pin changes, `[17]` stop when full |
| 8 | READSEL | `[2:0]` readback register |
| 9 | FIFO | `[7:0]` byte, `[8]` push, `[9]` reset FIFO and CRC, `[10]` FCS mode (fold every popped byte into the CRC and return the FCS after the data) |
| B | CRC | `[27:26]` 0 polynomial, 1 initial value, 2 final XOR; `[25:24]` byte lane; `[7:0]` byte (defaults: CRC-32 `0xEDB88320`, `0xFFFFFFFF`, `0xFFFFFFFF`) |
| A | TIMING | `[27]` channel; `[26]=0` TDC: `[3:0]` source (0-7 GPIO, 8-11 TARGET_IN, 12 TRIGGER_IN, 13 calibration toggle, 15 off), `[8]` trace its edges; `[26]=1` DTC: `[2:0]` TARGET_OUT pin, `[8]` enable, `[23:16]` delay tap |

Trigger sources: 0 immediately on arm, 1 TRIGGER_IN rising, 2 TRIGGER_IN
falling, 3 engine 0 `trace`, 4 engine 1 `trace`, 5 any watched pin change.

Readback registers:

| # | Contents |
|---|----------|
| 0 | STATUS: `[0]` run0 `[1]` run1 `[2]` done0 `[3]` done1 `[4]` collision fault `[5]` permission fault `[6]` illegal0 `[7]` illegal1 `[8]` mbox0 unread by engine `[9]` mbox1 unread `[10]` mbox0 pending to host `[11]` mbox1 pending `[12]` armed `[13]` triggered `[14]` overflow `[15]` full `[21:16]` entries `[31:24]` version (0x10) |
| 1 | `[7:0]` mailbox from engine 0, `[15:8]` from engine 1, `[23:16]` pc0, `[31:24]` pc1 |
| 2 | trace entry at TRACEPTR: `[31:16]` timestamp, `[15:14]` kind (0 engine event `{[13] engine, [7:0] data}`, 1 pin capture `{[12:0] pins}`, 2 timed edge `{[13] channel, [12] level, [11:4] fine}`) |
| 3 | `[15:0]` timestamp, `[20:16]` trace write pointer, `[21]` FCS mode, `[29:22]` FIFO level |
| 4 | ID `0x50494F31` ("PIO1") |
| 5 | `[7:0]` TDC0 fine, `[15:8]` TDC1 fine, `[16]` TDC0 level, `[17]` TDC1 level, `[31:24]` delay-line length in stages |

## Data FIFO and CRC-32

The host fills a 128-byte FIFO with `FIFO` frames faster than any engine
consumes it for slow protocols, or ahead of time for fast ones; `pop rd`
takes the next byte in one clock, so a 10 Mbit/s Manchester program can
stream a whole Ethernet frame (see `examples/manchester_tx.pio`).  Either
engine may pop; if both pop in the same clock they receive the same byte.
The CRC unit is a 32-bit reflected (LSB-first) register with a
host-programmable polynomial, initial value and final XOR (CRC frame);
the defaults are the Ethernet CRC-32 (`0xEDB88320`, `0xFFFFFFFF`,
`0xFFFFFFFF`).  It is updated by `crcu` (a byte), `crcbit` (the carry) or,
in FCS mode, by every data byte popped; once the FIFO is empty the next four
pops return the check value XORed with the final value, least-significant
byte first, which is Ethernet wire order.  `crcb rd, k` reads the same bytes
explicitly.  A shorter CRC uses the low bits: CRC-16/USB is `0xA001`,
`0xFFFF`, `0xFFFF`; CRC-5/USB is `0x14`, `0x1F`, `0x1F`; a non-reflected
CRC such as CAN's CRC-15 (`0x4599`) is run with the reflected polynomial
(`0x4CD1`), bits fed most-significant first with `crcbit`, and the register
read out bit-reversed.  Check values of "123456789": CRC-32 `0xCBF43926`,
CRC-16/USB `0xB4C8`, CRC-5/USB `0x19`, CRC-15/CAN `0x059E`.

## Sub-clock timing

The engines act on clock edges, 25 ns apart.  Two tapped delay lines of 128
CMOS5L delay cells (about 0.15 / 0.22 / 0.35 ns per stage at the fast /
typical / slow corner) let programs and the host see and place edges
*between* clock edges.

* **TDC (time-to-digital) channels 0 and 1** each watch one source selected
  with TIMING.  On every clock the taps are sampled; the number of stages the
  latest edge has already travelled is its arrival time within the period,
  counted in stages.  When the synchronised level changes, that count and the
  new level are latched (`in rd, TDC0`, readback register 5) and, if enabled
  and a capture is armed, written to the trace buffer as a kind-2 entry with
  the coarse timestamp.  Counting ones is immune to metastability bubbles.
* **Calibration.** Source 13 is a flop that toggles on the falling clock
  edge, so every sample sees an edge exactly half a period old; its fine
  value is the number of stages per 12.5 ns on this die at this voltage and
  temperature, the constant that turns stage counts into nanoseconds.
* **DTC (digital-to-time) channels 0 and 1** each replace one TARGET_OUT pin
  with a copy delayed by a programmable tap (TIMING, or `dtcw` from a
  program), so an edge lands `tap` stages after the clock edge that launched
  it.  Taps longer than a period are allowed; the pin simply lags by more
  than one clock.

A measured time is `coarse * 25 ns - fine * stage`, with `stage = 12.5 ns /
calibration`.  128 stages cover a whole period at the typical and slow
corners and about 80 % of it at the fast corner, where older edges read as
128 (saturated) but are still timestamped coarsely.  `examples/edge_timer.pio` and `examples/glitch_pulse.pio` show
the two directions.  The chains are asynchronous by construction: they are
false paths in `src/proto.sdc` and protected from the resizer by
`RSZ_DONT_TOUCH_RX` in `src/config.json`.

## Safe pin sharing

Each engine's output enables are ANDed with its host-set permission mask; an
enable outside the mask is dropped and latches the permission fault.  If both
engines enable the same pin in the same clock, that pin is left high impedance
(TARGET_OUT pins fall back to the host GPIO value) and the collision fault
latches.  Both faults are sticky until `RUN[4]`.  Pins that no engine enables
are driven by the host GPIO registers, so bring-up needs no program at all.
