# System design, piece by piece

This document explains the whole project from the ground up. It assumes you
can read basic SystemVerilog (`module`, `always @(posedge clk)`, `reg`,
`wire`, `assign`) but have never designed a chip, used a Tiny Tapeout board,
or run a synthesis flow. Every file in the repository is covered, in the
order a signal travels through the design: from the pins, through the host
link, into the engines, out to the pins again, and then into the tools that
prove it all works.

Contents

1. [What the chip is for](#1-what-the-chip-is-for)
2. [The Tiny Tapeout box we live in](#2-the-tiny-tapeout-box-we-live-in)
3. [The big picture](#3-the-big-picture)
4. [Clocks, resets, and why every input is delayed](#4-clocks-resets-and-why-every-input-is-delayed)
5. [The host link (`proto_cfg_serial.sv`)](#5-the-host-link-proto_cfg_serialsv)
6. [The command decoder in the top level](#6-the-command-decoder-in-the-top-level)
7. [Program memory (`proto_program_ram.sv`)](#7-program-memory-proto_program_ramsv)
8. [The PIO engine (`proto_pio_engine.sv`)](#8-the-pio-engine-proto_pio_enginesv)
9. [Sharing the pins safely](#9-sharing-the-pins-safely)
10. [Mailboxes](#10-mailboxes)
11. [The data FIFO and CRC-32](#11-the-data-fifo-and-crc-32)
12. [Trace, trigger and capture (`proto_trace_ram.sv`)](#12-trace-trigger-and-capture-proto_trace_ramsv)
13. [Reading things back](#13-reading-things-back)
13a. [Sub-clock timing: seeing and placing edges between clocks](#13a-sub-clock-timing-seeing-and-placing-edges-between-clocks)
14. [A worked example: one UART byte, clock by clock](#14-a-worked-example-one-uart-byte-clock-by-clock)
15. [The Ethernet frame, and why the loop is shaped the way it is](#15-the-ethernet-frame-and-why-the-loop-is-shaped-the-way-it-is)
16. [The software: assembler and reference model](#16-the-software-assembler-and-reference-model)
17. [How it is verified](#17-how-it-is-verified)
18. [From RTL to silicon: the physical flow](#18-from-rtl-to-silicon-the-physical-flow)
19. [Design decisions and the reasons behind them](#19-design-decisions-and-the-reasons-behind-them)
20. [Glossary](#20-glossary)

---

## 1. What the chip is for

Many hardware protocols (UART, SPI, I2C, and others) are simple enough that
software often "bit-bangs" them: a program toggles pins by hand with careful
timing instead of using a dedicated peripheral. Bit-banging on a normal CPU
is fragile because interrupts, caches and variable instruction timing make
it hard to promise that a pin will change at exactly the right nanosecond.

This chip is a small computer built specifically for bit-banging. Its
instruction set does only a few things: read pins, write pins, count clocks,
shift bits in and out, and branch. Crucially, every instruction takes
exactly one clock cycle, so the timing of a program is something you can
work out with a pencil. Load a different program and the same silicon speaks
a different protocol. The design is inspired by the PIO state machines in
the Raspberry Pi RP2040, with a few different choices explained in
section 19.

The chip contains two of these little computers ("engines"), a way for a
host microcontroller to load programs and talk to them, a hardware rule that
stops the two engines from fighting over a pin, a small logic analyser, and
a byte FIFO with a CRC-32 unit so that even a 10 Mbit/s Ethernet frame can be
produced from a program.

## 2. The Tiny Tapeout box we live in

Tiny Tapeout is a service that collects many small designs onto one shared
chip. Each design gets a rectangle of silicon ("tiles") and a fixed set of
pins. Our design uses an 8x4 tile allocation, about 1.7 mm by 0.7 mm, on
IHP's 130 nm CMOS5L process.

Every Tiny Tapeout design is a SystemVerilog module with the same port list.
Ours is at the bottom of `src/tt_um_kaikino_protocol_emu.sv`:

```systemverilog
module tt_um_kaikino_protocol_emu (
    input  wire [7:0] ui_in,    // 8 dedicated inputs
    output wire [7:0] uo_out,   // 8 dedicated outputs
    input  wire [7:0] uio_in,   // 8 bidirectional pins: value seen on the pin
    output wire [7:0] uio_out,  // 8 bidirectional pins: value we want to drive
    output wire [7:0] uio_oe,   // 8 bidirectional pins: 1 = we are driving it
    input  wire       ena,      // 1 when our design is selected (unused here)
    input  wire       clk,      // the clock, 40 MHz for us
    input  wire       rst_n     // reset, active LOW (0 = hold everything at reset)
);
```

The bidirectional pins deserve a moment. A physical pin can either be driven
by the chip or left floating so something else can drive it. For each `uio`
pin the chip has three wires: `uio_in` (what the pin currently is),
`uio_out` (what we would drive) and `uio_oe` ("output enable": whether we
actually drive it). When `uio_oe` is 0 the pin is high impedance and
`uio_out` is ignored. Open-drain buses like I2C are built entirely from this:
you never drive a 1, you either pull the line low (`oe=1, out=0`) or let go
(`oe=0`) and an external pull-up resistor makes it high.

We assign the 24 pins like this (also in `info.yaml`):

| Pin | Name | Used for |
|-----|------|----------|
| `ui_in[0..2]` | CFG_SCK, CFG_MOSI, CFG_CS_N | the host link (an SPI slave) |
| `ui_in[3..6]` | TARGET_IN0..3 | inputs a program can sample |
| `ui_in[7]` | TRIGGER_IN | starts a capture; a program can read it too |
| `uo_out[0]` | CFG_MISO | host link data back to the host |
| `uo_out[1..7]` | TARGET_OUT0..6 | outputs a program can drive |
| `uio[0..7]` | GPIO0..7 | bidirectional target pins |

The host is the RP2040 microcontroller on the Tiny Tapeout demo board. It
talks to us over three input pins and one output pin, and everything else
is for the protocol under test.

## 3. The big picture

```
                 host MCU (SPI)
                     |
   ui_in[2:0] ---> proto_cfg_serial ---- 32-bit command word + valid pulse
                     |                                      |
   uo_out[0] <------ MISO (readback register)        command decoder
                                                            |
        +--------------------+------------------+-----------+---------------+
        |                    |                  |                           |
   program RAM 0        program RAM 1       host GPIO regs         FIFO + CRC-32
   (128 x 16)           (128 x 16)          permissions            mailboxes
        |                    |                                     capture config
   PIO engine 0         PIO engine 1
        |                    |
        +--------+-----------+
                 |
         pin arbitration  <---- permissions, collision detection
                 |
   uio_out / uio_oe / uo_out[7:1]  (the target pins)

   uio_in, ui_in[7:3] ---> 3 synchroniser flops ---> engines, capture logic
                                                        |
                                              trace RAM (32 x 32) + timestamp
```

Everything runs from one 40 MHz clock. There is a single `always` block in
the top level that holds all the "housekeeping" state, one block per engine,
and small memories. There are no other clock domains: the SPI host link is
sampled by the 40 MHz clock rather than clocked by SCK, which is unusual and
is explained in section 5.

## 4. Clocks, resets, and why every input is delayed

**One clock.** `clk` runs at 40 MHz, so one cycle is 25 ns. The number 40
was chosen because 10 Mbit/s Manchester code needs a transition every 50 ns,
which is two clocks; we needed a period that divides 50 ns and left room
for two instructions per half symbol.

**Asynchronous reset.** Almost every flip-flop in the design is written as

```systemverilog
always @(posedge clk or negedge rst_n) begin
  if (!rst_n) begin
    ... reset values ...
  end else begin
    ... normal behaviour ...
  end
end
```

The `or negedge rst_n` means the reset takes effect immediately when `rst_n`
falls, even without a clock. This is the standard Tiny Tapeout style and it
guarantees the chip starts in a known state before the clock is stable.

**Input synchronisation.** The outside world does not change pins in step
with our clock. If a pin changes exactly when a flip-flop samples it, the
flip-flop can enter a half-way state ("metastability") for a while. The
cure is to pass every external input through two flip-flops in a row before
using it. The top level does that for all 13 target inputs at once:

```systemverilog
wire [12:0] pins_raw = {ui_in[7], ui_in[6:3], uio_in};   // TRIGGER, IN3..0, GPIO7..0
reg  [12:0] pins_s1, pins_q, pins_qq;
...
pins_s1 <= pins_raw;   // first synchroniser stage
pins_q  <= pins_s1;    // second stage: this is what the engines see
pins_qq <= pins_q;     // one more: the previous value, for edge detection
```

Two consequences that matter throughout the design:

* An engine sees a pin change two clocks (50 ns) after it happens on the
  pin. Every timing calculation in the examples includes that.
* "Rising edge" means `pins_q` is 1 and `pins_qq` was 0. Edges are detected
  between consecutive synchronised samples, never on the raw pin.

The three host-link inputs are synchronised the same way inside
`proto_cfg_serial`.

## 5. The host link (`proto_cfg_serial.sv`)

The host needs a way to load programs and poke at the chip. We use SPI
"mode 0": the host drives a clock (SCK), a data line (MOSI), and a chip
select (CS_N, low while a transfer is in progress); we answer on MISO. A
transfer is always exactly 32 bits, most significant bit first.

**Why we don't clock anything from SCK.** The textbook SPI slave uses SCK
as a clock for its shift register. That creates a second clock domain, and
moving information between clock domains needs synchronisers, careful
timing analysis and a lot of scrutiny. Instead we treat SCK, MOSI and CS_N
as ordinary slow inputs: we sample them with the 40 MHz clock and look for
SCK going from 0 to 1. The cost is that SCK must be slow compared to our
clock (we require SCK below 5 MHz, an eighth of 40 MHz); the benefit is a
single clock domain, which made the timing sign-off and the formal proof
much simpler.

The module, condensed:

```systemverilog
reg [2:0] sck_sync;  reg [1:0] mosi_sync;  reg [1:0] cs_sync;
wire sck_rise = sck_sync[1] & ~sck_sync[2];   // SCK just went high
wire sck_fall = ~sck_sync[1] & sck_sync[2];   // SCK just went low
wire selected = ~cs_sync[1];                  // CS_N is low

always @(posedge clk or negedge rst_n) begin
  ...
  sck_sync  <= {sck_sync[1:0], cfg_sck};      // shift new sample in
  mosi_sync <= {mosi_sync[0], cfg_mosi};
  cs_sync   <= {cs_sync[0], cfg_cs_n};
  cmd_valid <= 1'b0;
  if (!selected) begin
    bit_count <= 5'd0;            // CS high: get ready for a new frame
    shift_out <= read_data;       // and capture what the host will read next
  end else begin
    if (sck_rise) begin           // host presents a bit on every rising SCK
      shift_in  <= {shift_in[29:0], mosi_sync[1]};
      bit_count <= bit_count + 5'd1;
      if (bit_count == 5'd31) begin           // that was the 32nd bit
        cmd_word  <= {shift_in, mosi_sync[1]};
        cmd_valid <= 1'b1;                    // one-clock pulse
      end
    end
    if (sck_fall) shift_out <= {shift_out[30:0], 1'b0};   // present next MISO bit
  end
end
assign cfg_miso = shift_out[31];
```

Reading it:

* `sck_sync` is three bits deep because we need the current and the previous
  synchronised value to see an edge (bits 1 and 2), and bit 0 is the first
  synchroniser stage.
* On each rising SCK we shift the MOSI bit into `shift_in`. Notice that
  `shift_in` is only 31 bits: the 32nd bit is never stored, it goes straight
  into `cmd_word` together with the other 31, and `cmd_valid` pulses for one
  clock. That pulse is what the rest of the chip reacts to.
* While CS_N is high we keep loading `shift_out` with `read_data`, the
  register the host has asked to read. The moment CS_N falls the load stops
  and MISO shows bit 31; on each falling SCK we shift, so the host, which
  samples on rising SCK, sees a clean bit every time. Because of this, a
  transfer always returns the value captured *before* it started, which is
  why reading a register takes two frames (section 13).
* If CS_N rises in the middle of a frame the counter resets, so a glitched
  or aborted transfer cannot leave the link out of step.

There is deliberately nothing clever here. One small piece of state, no
handshakes, no FIFOs.

## 6. The command decoder in the top level

Every frame is `{op[3:0], argument[27:0]}`. When `cmd_valid` (called
`cfg_request` in the top level) pulses, this `case` runs once:

| op | name | what the argument bits mean |
|----|------|------------------------------|
| 0 | NOP | nothing (used purely to read a register back) |
| 1 | GPIO | `[7:0]` GPIO output enables, `[15:8]` GPIO values, `[22:16]` TARGET_OUT values, all driven by the host on pins no engine owns |
| 2 | PROGRAM | `[27]` engine, `[26:19]` address, `[18:3]` 16-bit instruction |
| 3 | RUN | `[0]` start 0, `[1]` stop 0, `[2]` start 1, `[3]` stop 1, `[4]` clear faults, `[5]` reset timestamp, `[6]` arm capture, `[7]` disarm, `[8]`/`[9]` acknowledge mailbox 0/1, `[10]` clear DONE flags, `[23:16]` start address |
| 4 | TRACEPTR | `[4:0]` which trace entry the host wants to read |
| 5 | PERM | `[27]` engine, `[7:0]` GPIO pins it may drive, `[14:8]` TARGET_OUT pins it may drive |
| 6 | MBOX | `[27]` engine, `[7:0]` a byte for it |
| 7 | CAPTURE | `[12:0]` which pins to watch, `[15:13]` trigger source, `[16]` capture pin changes, `[17]` stop when full |
| 8 | READSEL | `[2:0]` which register future frames return on MISO |
| 9 | FIFO | `[7:0]` byte, `[8]` push it, `[9]` reset FIFO and CRC, `[10]` FCS mode |

Two things to notice in the code:

```systemverilog
CMD_PROGRAM: begin
  prog_waddr <= cfg_word[26:19];
  prog_wdata <= cfg_word[18:3];
  if (!cfg_word[27] && !e_running[0]) prog_we[0] <= 1'b1;
  if ( cfg_word[27] && !e_running[1]) prog_we[1] <= 1'b1;
end
```

The write enable is a register that is set for one clock and cleared again
at the top of the block (`prog_we[k] <= 1'b0;` runs every cycle, and the
`case` overrides it when needed). This "default off, case turns it on" idiom
is used for `prog_we`, `eng_start`, `eng_stop` and `eng_clear_done`: all of
them are single-clock pulses. And a program write is silently ignored while
that engine is running. Changing the code under a running engine would make
its timing unpredictable, so the hardware refuses.

```systemverilog
CMD_RUN: begin
  start_pc     <= cfg_word[23:16];
  eng_start[0] <= cfg_word[0];
  ...
```

RUN packs many independent actions into one frame because each frame costs
32 SCK edges. Starting engine 0, resetting the timestamp and arming the
capture buffer in one go keeps them aligned in time.

## 7. Program memory (`proto_program_ram.sv`)

Each engine has its own instruction memory: 128 words of 16 bits.

```systemverilog
reg [15:0] mem [0:DEPTH-1];
always @(posedge clk) begin
  if (we) mem[waddr] <= wdata;     // host writes, one word per PROGRAM frame
end
assign rdata = mem[raddr];         // read is combinational: address in, data out
```

That is a complete memory. The read has no clock: whatever address the
engine's program counter holds, the instruction appears on `rdata` in the
same cycle, so the engine can fetch and execute in one clock without a
pipeline.

Two things a chip designer worries about that a software person never
would:

**What this becomes physically.** Silicon foundries usually offer an SRAM
"macro", a dense block of memory cells. The CMOS5L process we are using has
no SRAM macro in its open PDK, so the synthesis tool turns each of the 2048
bits into a flip-flop and builds a 128-way multiplexer to read it. That is
why the memories are 128 words rather than the 256 originally planned:
256-word versions would have needed more area than the tile allows. The
wrapper is kept "SRAM shaped" (one write port, one read port) so a macro
could replace it later without touching anything else.

**What happens on an FPGA.** The `ifdef SYNTH` branch (only defined by the
Tiny Tapeout FPGA flow) reads the memory on the *falling* edge of the clock:

```systemverilog
reg [15:0] rdata_q;
always @(negedge clk) rdata_q <= mem[raddr];
assign rdata = rdata_q;
```

FPGA block RAMs need a clock edge to read, but the engine wants the data in
the same cycle it changes the address. Reading on the falling edge gives the
RAM half a cycle after the address settles and still delivers the data
before the next rising edge, so the FPGA behaves cycle for cycle like the
ASIC. This trick lets the same test suite pass on the FPGA netlist.

## 8. The PIO engine (`proto_pio_engine.sv`)

This is the heart of the chip. Think of it as a CPU with almost nothing in
it:

* `program_counter` (8 bits, only the low 7 address the memory)
* four 8-bit registers `r0..r3`
* one carry bit `C`
* a 12-bit `delay_count` for stalling
* the output registers: GPIO data (8), GPIO output enable (8), TARGET_OUT
  data (7), TARGET_OUT output enable (7)
* flags: `running`, `done`, `fault_illegal`

### 8.1 Instruction format

Every instruction is 16 bits:

```
 15  12 11  10 9   8 7          0
+------+------+-----+-----------+
|opcode| rd   | sub | immediate |
+------+------+-----+-----------+
```

`rd` names a register (0..3), `sub` is a two-bit sub-operation, and the
immediate is an 8-bit constant. Different opcodes reinterpret the fields,
but they always sit in the same place, which keeps the decoder tiny. The
helper wires at the top of the module pull the fields out once:

```systemverilog
wire [3:0] opcode = instruction[15:12];
wire [1:0] rd     = instruction[11:10];
wire [1:0] sub    = instruction[9:8];
wire [7:0] imm    = instruction[7:0];
wire [7:0] rd_val = regs[rd];
wire [7:0] rs_val = regs[sub];     // for ALU ops the sub field is a second register
```

### 8.2 The execute loop

The whole engine is one `always` block. Its skeleton:

```systemverilog
always @(posedge clk or negedge rst_n) begin
  if (!rst_n) begin ...reset... end
  else begin
    trace_we <= 0; mbox_in_take <= 0; mbox_out_we <= 0;   // pulses default to off
    if (clear_done) done <= 0;
    if (stop) begin                      // host said stop
      running <= 0; uio_oe <= 0; uo_oe <= 0;
    end else if (start) begin            // host said start
      program_counter <= start_pc; delay_count <= 0; carry <= 0;
      running <= 1; done <= 0;
    end else if (running) begin
      if (delay_count != 0) delay_count <= delay_count - 1;   // stalled
      else case (opcode) ... endcase                           // execute one instruction
    end
  end
end
```

The priorities are what you would hope: stop beats start beats running. A
stopped engine releases every output enable, so its pins go high impedance
the moment it halts; the data registers are left alone.

Inside the `case`, almost every branch ends with `program_counter <= pc_next`
where `pc_next = program_counter + 1`. The ones that do not are the
branches (they load the target address) and `WAIT` when its condition is
false (it leaves the PC alone so the same instruction runs again next clock).

### 8.3 The instructions, one by one

The comment block at the top of `proto_pio_engine.sv` is the authoritative
list; here is what each does and why it exists.

**`NOP`** does nothing for one clock. Used as timing padding.

**`MOVI rd, imm8`** loads a constant.

**`OUT rs, target, mask8`** writes a register to one of four pin-target
registers, but only the bits selected by the mask:

```systemverilog
2'd0: uio_data <= (uio_data & ~imm) | (rd_val & imm);
```

The mask lets a program own some GPIO bits without disturbing others,
which matters when the second engine owns the rest.

**`IN rd, source`** reads GPIO inputs, the `AUX` byte (trigger, the four
sample-only inputs, and flags), the mailbox byte (which also tells the top
level the byte has been taken), or the current GPIO data register.

**`DELAY n`** loads `delay_count` and moves to the next instruction. The
engine then does nothing for `n` more clocks. Four flavours: an 8-bit
immediate, a register, or either of those times 16 (the value is shifted
left by 4), which is how a 12-bit count fits in an 8-bit field. Total time
for `DELAY n` is `n + 1` clocks: one to execute it, `n` to stall.

**`WAIT cond, pin`** stalls until a pin is low, high, has just risen, or
has just fallen. The `pin` field selects one of 16 sources: the 8 GPIOs,
the 4 sample-only inputs, TRIGGER_IN, and three internal flags (mailbox has
a byte, our outgoing mailbox is still unread, the other engine is running).
The edge conditions use `pin_prev`, which the top level feeds from
`pins_qq`.

**`JMP addr`** and the register branches **`JNZ`, `DJNZ`, `JZ`, `DJZ`**.
`DJNZ rd, addr` ("decrement and jump if not zero") is the loop instruction:

```systemverilog
2'd1: begin regs[rd] <= dec_val; program_counter <= (dec_val != 8'd0) ? imm : pc_next; end
```

It decrements and branches in a single clock, so a loop body plus `DJNZ`
has no hidden overhead.

**`JPH pin, addr`** and **`JPL pin, addr`** branch if a pin is high or low.
Taken or not, a branch costs one clock, which keeps timing easy to reason
about.

**`SETP` and `SHOUT`** write a single bit of a pin target. The source of the
bit is selected by `imm[6:5]`: a constant, the carry, or the "rotate and
shift out" modes that make serial protocols cheap:

```systemverilog
wire setp_base = (imm[6:5] == 2'd1) ? carry :
                 (imm[6:5] == 2'd2) ? rd_val[7] : rd_val[0];
wire setp_val  = (imm[6:5] == 2'd0) ? imm[7] : (setp_base ^ imm[7]);
...
if (imm[6:5] == 2'd2) begin regs[rd] <= {rd_val[6:0], rd_val[7]}; carry <= rd_val[7]; end  // rotate left
if (imm[6:5] == 2'd3) begin regs[rd] <= {rd_val[0], rd_val[7:1]}; carry <= rd_val[0]; end  // rotate right
```

`shout_lsb r0, UIO, 0` writes bit 0 of `r0` to GPIO0, rotates `r0` right,
and remembers the bit in `C`. Eight of these transmit a byte LSB first, and
because it *rotates* rather than shifts, `r0` still holds the original byte
afterwards. `imm[7]` inverts the bit for the carry and rotate sources; the
Manchester transmitter uses that to write the first half symbol (`~bit`)
and then `setp UO, 0, C` for the second half (`bit`) without a second
register.

**`SHL`, `SHR`, `SHIN_LSB`, `SHIN_MSB`** are the receive-side shifts.
`shin_lsb r0, IN0` samples a pin into the top of `r0` while shifting right,
so eight of them collect a byte that arrived LSB first (UART, Ethernet).

**ALU: `MOV ADD SUB AND OR XOR MOVC NOT`** plus the four data-path ops
**`CRCU`, `CRCI`, `CRCB`, `POP`** (section 11). `ADD` and `SUB` set the
carry; `MOVC` copies the carry into a register so it can be branched on.

**`TRACE`, `MBOX`, `DONE`** raise events: write a timestamped entry to the
trace buffer, post a byte to the host mailbox, or set the DONE flag. These
produce one-clock pulses (`trace_we`, `mbox_out_we`) that the top level
turns into memory writes on the following clock.

**`HALT`** clears `running` and every output enable. An undefined opcode
(15) does the same and also sets the sticky `fault_illegal` flag so the
host can tell a crash from a normal halt.

### 8.4 Why "one instruction per clock" is a big deal

Because every instruction takes exactly one clock and `DELAY` is exact,
the time between any two pin writes in a straight-line program is just the
number of instructions between them plus the delays. The UART transmitter
in section 14 needs a bit period of `BIT + 3` clocks and that formula is
simply read off the program. There is no cache, no pipeline stall, and no
interrupt; the only thing that can vary is a `WAIT`, and that is the whole
point of a `WAIT`.

## 9. Sharing the pins safely

Two engines and the host can all want the same pin. The top level resolves
it with a few lines of pure combinational logic.

First, each engine's output enable is filtered by a permission mask the
host sets with PERM (default: everything allowed):

```systemverilog
wire [7:0] req_uio0 = e_uio_oe[0] & perm_uio[0];   // what engine 0 is allowed to drive
wire [7:0] req_uio1 = e_uio_oe[1] & perm_uio[1];
```

Then collisions are found and both requests are cancelled where they
overlap:

```systemverilog
wire [7:0] coll_uio = req_uio0 & req_uio1;      // both want the same pin
wire [7:0] drv_uio0 = req_uio0 & ~coll_uio;     // engine 0 drives only uncontested pins
wire [7:0] drv_uio1 = req_uio1 & ~coll_uio;
```

Finally the pin gets the value of whoever is driving it, and the host's
GPIO registers fill in any pin no engine asked for:

```systemverilog
assign uio_out = (drv_uio0 & e_uio_data[0]) | (drv_uio1 & e_uio_data[1]) |
                 (~(req_uio0 | req_uio1) & host_uio_data);
assign uio_oe  = drv_uio0 | drv_uio1 | (~(req_uio0 | req_uio1) & host_uio_oe);
```

Because `drv_uio0` and `drv_uio1` can never share a set bit, the OR of the
two data terms is safe: exactly one of them contributes to each pin. A
contested pin ends up with `oe = 0`, high impedance, which is the safe
answer for push-pull protocols (two drivers fighting would short the pin).

Two sticky flags record that something went wrong:

```systemverilog
if (|coll_uio | |coll_uo) fault_collision <= 1'b1;   // engines collided
if (perm_violation)       fault_perm      <= 1'b1;   // an engine tried a forbidden pin
```

They stay set until the host sends RUN with bit 4. The same structure is
repeated for the seven TARGET_OUT pins with 7-bit vectors.

Two of the formal properties in section 17 are direct statements about this
code: a contested pin is never driven, and a driven pin is always inside
someone's permission.

## 10. Mailboxes

Each engine has a one-byte mailbox in each direction. Host to engine:

```systemverilog
CMD_MBOX: begin
  mbox_tx[cfg_word[27]]       <= cfg_word[7:0];
  mbox_tx_valid[cfg_word[27]] <= 1'b1;        // "there is a byte"
end
...
if (e_mbox_take[k]) mbox_tx_valid[k] <= 1'b0; // engine did IN rd, MBOX
```

The valid flag is visible to the engine as pin 13, so `wait HIGH, MBOX_IN`
followed by `in r0, MBOX` is the idiom for "block until the host sends a
byte". Engine to host is the mirror image: `mbox r0` sets `mbox_rx_pending`,
the host reads register 1 and acknowledges with RUN bit 8 or 9.

A mailbox is the right size for control (a byte to transmit, a count, a
result) and far too slow for streaming: a frame on the 5 MHz host link
takes about 6.4 microseconds, while a 10 Mbit/s protocol consumes a byte
every 0.8 microseconds. That gap is why the FIFO exists.

## 11. The data FIFO and CRC-32

### 11.1 The FIFO

A 128-byte first-in first-out buffer, filled by the host with FIFO frames
before (or during, for slow protocols) a transfer, and drained by `POP`.

```systemverilog
reg [7:0] fifo_mem [0:127];
reg [6:0] fifo_rptr, fifo_wptr;   // read and write positions
reg [7:0] fifo_count;             // 0..128, so one bit wider than a pointer
wire fifo_empty = (fifo_count == 0);
wire fifo_full  = fifo_count[7];  // bit 7 set only when count == 128
```

This is the classic circular buffer: pushing writes at `wptr` and advances
it; popping reads at `rptr` and advances it; the pointers wrap naturally
because they are 7 bits wide. `fifo_count` is kept separately so that empty
and full are unambiguous (with only pointers, `rptr == wptr` could mean
either).

Push and pop can happen in the same clock (the host is slow, but nothing
forbids it), which is the one subtle line:

```systemverilog
fifo_count <= fifo_count + 1'b1 - {{FIFO_AW{1'b0}}, (fifo_pop & ~fifo_empty)};
```

If a pop is also happening, the net change is zero.

`POP` is unusual among the engine's instructions: it acts *in the same
clock* it executes. The engine exports a combinational `fifo_pop` signal
(decoded straight from the instruction, no register), the top level
advances the read pointer on that very edge, and the engine loads the byte
that was at the head. Two `POP`s in consecutive clocks therefore get two
different bytes, which a registered pulse could not guarantee. `POP` also
sets the carry to "was there a byte", so a program can detect an empty
FIFO with `movc` and `jz`.

### 11.2 CRC-32

A CRC is a checksum that receivers use to detect corrupted frames; Ethernet
appends a 32-bit one (the FCS) to every frame. Computing it in software on
this engine at 10 Mbit/s would not fit the four clocks per bit budget, so
there is a hardware unit. The whole algorithm is one function:

```systemverilog
function automatic [31:0] crc32_byte(input [31:0] c, input [7:0] d);
  integer b; reg [31:0] x;
  begin
    x = c ^ {24'd0, d};
    for (b = 0; b < 8; b = b + 1)
      x = (x >> 1) ^ (x[0] ? 32'hEDB88320 : 32'd0);
    crc32_byte = x;
  end
endfunction
```

The `for` loop is unrolled by synthesis into eight layers of XOR gates, so
folding a byte into the running CRC takes one clock. `0xEDB88320` is the
"reflected" Ethernet polynomial; the same constant, initial value
`0xFFFFFFFF` and final inversion make this identical to `zlib.crc32`, which
the tests rely on.

The register is driven from two places:

```systemverilog
wire crc_fold = crc_update | (fifo_pop & ~fifo_empty & fcs_mode);
...
if (crc_fold) crc <= crc32_byte(crc, crc_fold_byte);
if (crc_init) begin crc <= 32'hFFFFFFFF; fcs_idx <= 0; fcs_done <= 0; end
```

Either an explicit `CRCU rd` instruction, or, in "FCS mode", every data byte
popped from the FIFO. FCS mode is the Ethernet convenience: once the FIFO is
empty, the next four `POP`s return the inverted CRC one byte at a time,
least significant byte first, which is exactly the order Ethernet transmits
the FCS:

```systemverilog
wire [31:0] fcs       = ~crc;
wire [7:0]  fcs_byte  = fcs[fcs_idx*8 +: 8];          // byte fcs_idx of the FCS
wire        fcs_avail = fcs_mode & ~fcs_done;
wire        fifo_valid = ~fifo_empty | fcs_avail;
wire [7:0]  fifo_data  = ~fifo_empty ? fifo_head : (fcs_avail ? fcs_byte : 8'd0);
```

So a transmit program does not know or care where the payload ends: it
pops `length + 4` bytes and the last four are the checksum. `CRCI`, `CRCU`
and `CRCB rd, k` expose the same unit to programs that need a CRC in some
other framing.

## 12. Trace, trigger and capture (`proto_trace_ram.sv`)

A 32-entry buffer of 32-bit words records *when* things happened. It doubles
as a print statement for programs and as a small logic analyser.

Each entry is `{timestamp[15:0], kind[1:0], engine, data[12:0]}`. The
timestamp is a free-running 16-bit counter the host can zero with RUN
bit 5. Three sources can write an entry:

```systemverilog
wire [31:0] trace_wdata = e_trace_we[0] ? {timestamp, 2'd0, 1'b0, 5'd0, e_trace_data[0]} :  // engine 0 TRACE
                          e_trace_we[1] ? {timestamp, 2'd0, 1'b1, 5'd0, e_trace_data[1]} :  // engine 1 TRACE
                                          {timestamp, 2'd1, 1'b0, pins_q};                   // pin capture
```

Only one entry can be written per clock. If two sources collide, engine 0
wins and `trace_overflow` is set so the host knows something was dropped.

The capture side is a two-state machine, `armed` then `triggered`:

```systemverilog
wire trig_hit = (trig_src == 0) |                       // immediately
                (trig_src == 1 && trig_rise) |          // TRIGGER_IN rising
                (trig_src == 2 && trig_fall) |          // TRIGGER_IN falling
                (trig_src == 3 && e_trace_we[0]) |      // engine 0 TRACE
                (trig_src == 4 && e_trace_we[1]) |      // engine 1 TRACE
                (trig_src == 5 && pin_change != 0);     // any watched pin moved
wire capturing = triggered | (armed & trig_hit);
wire want_pin  = capturing & pin_capture_en & (pin_change != 0);
```

`pin_change` is the XOR of the two most recent synchronised samples, masked
by the watch list. Once triggered, every clock in which a watched pin
changes writes an entry with all 13 pins and the time, until the buffer is
full (by default it stops; the host can let it wrap). Reading is done one
entry at a time: TRACEPTR selects, register 2 returns it.

`proto_trace_ram.sv` itself is the same flop-array memory as the program
RAM, just 32 bits wide, with the same falling-edge read under `SYNTH`.

## 13. Reading things back

Everything the host can see is one of five 32-bit registers, selected with
READSEL:

```systemverilog
wire [31:0] read_mux = (read_sel == 0) ? status_word :
                       (read_sel == 1) ? {pc[1], pc[0], mbox_rx[1], mbox_rx[0]} :
                       (read_sel == 2) ? trace_rdata :
                       (read_sel == 3) ? {fifo_count, fcs_mode, trace_wptr, timestamp} :
                                         32'h50494F31;   // "PIO1", a sanity check
```

`status_word` packs running/done/fault/mailbox/capture flags and the trace
entry count. Because the link captures `read_mux` while CS_N is high
(section 5), reading is: send READSEL, then send any frame (a NOP) and look
at what comes back on MISO during it. The test harness's `read()` helper
does exactly those two frames.

## 13a. Sub-clock timing: seeing and placing edges between clocks

Everything above happens on clock edges, 25 ns apart. That is the one
limitation this design shares with every PIO-style engine, and the part of
the chip that removes it is also the only part that is not ordinary digital
logic.

**The delay line (`proto_delay_chain.sv`).** A chain of 128 delay cells from
the CMOS5L library, each instantiated by name:

```systemverilog
(* keep *) sg13cmos5l_dlygate4sd2_1 tdly_cell (.A(tdly_tap[i]), .X(tdly_tap[i+1]));
```

A signal entering `tdly_tap[0]` reaches tap `i` about `i` times 0.22 ns later
(0.15 ns at the fast corner, 0.35 ns at the slow one). The `(* keep *)`
attribute stops synthesis from optimising a chain of buffers that "does
nothing", and two flow settings stop place-and-route from doing the same:
`RSZ_DONT_TOUCH_RX` in `src/config.json` matches the `tdly` token in every
chain name so the resizer never deletes or re-buffers a stage, and
`src/proto.sdc` declares every path through a chain a false path so timing
repair has no reason to touch it. Without those two lines the tools removed
most of the chain in the first experiment (`experiments/tdc/README.md`).

**Measuring an edge (`proto_tdc.sv`).** Feed a pin into the chain and sample
all 128 taps on the clock edge. If the pin rose 8 ns before the edge, the
first 36 taps already show the new level and the rest still show the old
one. Counting the taps that match the new level gives the arrival time in
units of one stage:

```systemverilog
wire [STAGES-1:0] matched = lvl_q ? snap_q : ~snap_q;   // taps the edge has reached
for (k = 0; k < STAGES; k = k + 1) cnt = cnt + matched[k];
```

Counting rather than looking for the boundary matters: a tap sampled exactly
as it changes can come out wrong (metastability), which shows up as a bubble
in the pattern, and a count is off by at most one either way. The taps are
sampled once and the *count* is registered a clock later (a metastable tap
gets that whole clock to settle), which puts the result two flops behind the
pad, exactly like the ordinary input synchronisers, so it lines up with the
synchronised pin that the rest of the chip sees. An earlier version stored
the taps twice; halving those flops was what let the layout route. The top level latches the count and level whenever that pin changes,
exposes them to programs (`in rd, TDC0`) and the host (register 5), and can
write them into the trace buffer next to the coarse timestamp.

**Calibration.** The stage delay varies more than two to one with process,
voltage and temperature, so counts are only meaningful once the chip knows
its own stage. Source 13 of each TDC channel is a flop that toggles on the
falling clock edge: every sample sees an edge exactly 12.5 ns old, so its
count *is* the number of stages per half period, measured on this die right
now. Every other measurement is a ratio against it. (A half period rather
than a full one keeps the reference inside the 128-stage range even at the
fast corner, where a full period would need 166 stages.)

**Placing an edge (`proto_dtc.sv`).** The reverse: the value a program wants
on a TARGET_OUT pin enters a second chain, and a 129-way multiplexer picks
the tap that drives the pad. Tap 60 means the pin changes 60 stages, about
13 ns, after the clock edge that launched it. A program sets the tap with
`dtcw`; the host with a TIMING frame.

**Simulating something the simulator cannot see.** The foundry's cell models
are zero-delay, so in a plain simulation every tap changes at once and the
count is always 128. The RTL build for tests therefore swaps the cells for
`assign #0.224` statements (`-DTDLY_PS=224`), and the reference model
computes the same counts from the exact times the test harness changes the
pads (always 1 ns after a clock edge). The fine values are then compared
exactly, every clock, like everything else. Gate-level and FPGA runs set the
stage to zero and check only the plumbing.

## 14. A worked example: one UART byte, clock by clock

`examples/uart_tx.pio`, with `BIT = 17` for a 20-clock bit (2 Mbaud at
40 MHz):

```
        movi  r1, 0x01
        out   r1, UIO, 0x01          ; idle high
        out   r1, UIO_OE, 0x01       ; drive GPIO0
loop:   wait  HIGH, MBOX_IN          ; block until the host sends a byte
        in    r0, MBOX
        movi  r2, 8
        setp  UIO, 0, 0              ; start bit
        delay BIT
        nop
nbit:   shout_lsb r0, UIO, 0         ; data bit, LSB first
        delay BIT
        djnz  r2, nbit
        setp  UIO, 0, 1              ; stop bit
        delay BIT
        jmp   loop
```

Count clocks between consecutive pin writes:

* `setp` (start bit) at clock 0. `delay 17` runs at clock 1 and stalls
  clocks 2..18. `nop` at 19. `shout_lsb` (bit 0) at clock 20. Start bit
  width: 20 clocks.
* `shout_lsb` at 20, `delay` at 21 stalling 22..38, `djnz` at 39 (taken),
  `shout_lsb` (bit 1) at 40. Bit width: 20 clocks. The same for bits 2..7.
* After bit 7's `delay`, `djnz` falls through at 39 and `setp` (stop bit)
  is at 40. Still 20 clocks.

So the bit period is `BIT + 3` in general: one clock for the write, `BIT + 1`
for the delay, one for the branch. The `nop` after the start bit exists
purely to make the start bit the same width as a data bit. The test
`test_uart_tx` decodes GPIO0 with a Python UART monitor at exactly this
period and checks the bytes, while the reference model is compared against
the RTL on every one of those clocks.

## 15. The Ethernet frame, and why the loop is shaped the way it is

At 10 Mbit/s and 40 MHz every bit is four clocks and every Manchester
half-symbol is two. Two instructions per half-symbol is all the budget
there is, and one of them must be the pin write. `examples/manchester_tx.pio`
therefore emits each bit as:

```
shout_lsb r0, UO, 0, INV    ; first half  = ~bit   (rotate, C = bit)
<free slot>
setp      UO, 0, C          ; second half =  bit
<free slot>
```

The free slots do the bookkeeping. A byte is eight of these groups, 32
words, with 16 free slots; the transmitter uses two blocks, A and B: A
emits `r0` and uses a free slot to `pop r1` for the next byte, B emits `r1`
and pops `r0`, and B's last free slot is the `djnz` that loops back to A.
The byte boundary therefore costs zero clocks, the FIFO supplies the data,
and in FCS mode the last four pops are the checksum. The preamble is a
compact 8-word loop (the same 0x55 byte 28 bit-pairs in a row) and the
start-frame delimiter is unrolled because its counter register was still in
use. One subtlety the tests caught: `POP` sets the carry, so it must sit in
the free slot *after* `setp UO, 0, C`, not before.

The receiver (`manchester_rx.pio`) samples one clock after the first rising
edge and then every four clocks, which lands in the second half of every
bit; it traces a byte every eight samples and the host re-aligns on the
start delimiter in software. On the second engine, in the test, it decodes
the first engine's frame from a loopback wire.

## 16. The software: assembler and reference model

**`tools/proto_asm.py`** turns the text above into 16-bit words. It is a
two-pass assembler: pass one records labels and `.equ` constants and
assigns addresses, pass two encodes each line. `encode()` is a long chain
of `if mnemonic == ...` that mirrors the instruction table; `disassemble()`
does the reverse and the unit tests check that every instruction survives a
round trip. `program_frames()` wraps words into PROGRAM host frames.

**`tools/proto_ref.py`** is the most important file for verification: a
Python re-implementation of the whole chip at the level of one clock edge.
`Engine.step()` mirrors the engine's `always` block line for line, and
`Chip.step()` mirrors the top level: the three synchroniser stages, the pin
arbitration, mailboxes, FIFO, CRC, capture, and the command decoder. If the
RTL and the model ever disagree about a pin value or the timestamp, a test
fails on that exact clock. Writing the same design twice in two languages
sounds wasteful, but the model found two real RTL bugs before anything was
committed (a branch decoded the wrong bit field; the trace timestamp was
off by one) and made the FPGA and gate-level netlists checkable with the
same tests.

## 17. How it is verified

`test/` is a cocotb suite: Python coroutines drive the SystemVerilog design
in the Icarus simulator.

* `proto_host.py` bit-bangs the SPI link and, after every clock edge,
  advances the reference model with the same pad values and compares
  `uio_out`, `uio_oe`, `uo_out[7:1]` and the timestamp. It knows exactly
  when a frame takes effect (three clocks after the 32nd SCK edge) so the
  model applies the command on the same cycle; in RTL runs it cross-checks
  that against the internal `cfg_request` pulse.
* `protocols.py` holds independent decoders and peers: a UART monitor that
  samples mid-bit, an SPI slave, an I2C slave that ACKs an address, a
  Manchester decoder, and an FCS checker built on `zlib.crc32`. These never
  look at the RTL's internals, only at the pins.
* `test_core.py` covers the host link, program loading and write lock,
  collisions and permissions, mailboxes, the illegal-opcode fault, trigger
  capture, and the FIFO/CRC.
* `test_protocols.py` runs the UART, SPI and I2C programs; `test_ethernet.py`
  the 64-byte frame loopback; `test_random.py` random programs on both
  engines with random pins, masks and FIFO traffic, then compares the final
  status word and the whole trace buffer to the model.
* `formal/proto.sby` asks SymbiYosys to *prove* nine properties written in
  the `ifdef FORMAL` block at the end of the top level: no contested pin is
  driven, drives stay inside permissions, a stopped engine drives nothing,
  the trace and FIFO counters stay in range, program writes only follow a
  PROGRAM frame while stopped, collisions latch the fault, and the timestamp
  is free running. "Prove" means for all possible inputs, not just the ones
  a test happened to try; the tool searches for a counterexample and, using
  induction, shows none exists.
* The same cocotb suite runs on the iCE40 FPGA netlist (`make FPGA=yes`) and
  on the final gate-level netlist from the ASIC flow (`make GATES=yes`), so
  the thing that will be manufactured, not just the source, has been
  exercised.

## 18. From RTL to silicon: the physical flow

Turning SystemVerilog into a chip layout is a pipeline of tools, run for us
by LibreLane (an OpenROAD-based flow) inside the Tiny Tapeout scripts:

1. **Lint** (Verilator) rejects anything ambiguous before we start.
2. **Synthesis** (Yosys) rewrites the RTL as a netlist of the foundry's
   standard cells: NAND gates, flip-flops, multiplexers. Our design becomes
   about 30 000 cells, 6 700 of them flip-flops (mostly the memories).
3. **Floorplan** places the cell rows inside the 8x4 tile outline and puts
   the pins where the Tiny Tapeout multiplexer expects them. The outline is
   a DEF template; the official tools had no 8x4 one, so
   `flow/gen_tile_def.py` derives it from the 8x2 template using exactly
   the transformation that turns 6x2 into 6x4 (verified byte-identical).
4. **Placement** chooses an x,y for every cell; **clock tree synthesis**
   builds a balanced tree of buffers so all 6 700 flip-flops see the clock
   at nearly the same instant; **routing** draws the wires on the metal
   layers.
5. **Timing analysis** checks, at slow/typical/fast process and temperature
   corners, that every signal arrives before the next clock edge (setup)
   and not so early that it corrupts the current one (hold). Our worst
   setup path has 7.7 ns of margin in a 25 ns period.
6. **DRC** (design rule check) verifies the geometry obeys the foundry's
   manufacturing rules; **LVS** (layout versus schematic) extracts the
   transistors back out of the layout and confirms they match the netlist.
7. **Gate-level simulation** runs the cocotb suite on the final netlist.

`flow/harden.sh` runs all of this locally in Docker; `.github/workflows/gds.yaml`
runs it on GitHub. `docs/signoff.md` records the numbers.

## 19. Design decisions and the reasons behind them

**Why not just put a UART, an SPI and an I2C block on the chip?** Because
then the chip could only ever speak those three. Programs can be written
after fabrication; the Ethernet transmitter is a program.

**Why two engines and not four?** Area. Each engine's memory is 2 048
flip-flops. Two engines already cover the interesting cases (a transmitter
and a receiver, a master and a stimulus generator) and left room for the
FIFO.

**Why 128 program words?** The plan said 256, but this process has no SRAM
macro and 256-word flop arrays blew the area budget. Every example fits
comfortably; the Ethernet transmitter is the largest at 118 words.

**Why sample SCK instead of clocking from it?** One clock domain means one
timing analysis, one formal model, and no synchroniser bugs. The price, a
5 MHz ceiling on the host link, is irrelevant for a link that only loads
programs and control bytes.

**Why does `POP` act combinationally while `TRACE` and `MBOX` are
registered?** `POP` must return a different byte on every consecutive
clock; the others only need to happen "soon" and a registered pulse is
simpler to time.

**Why rotate instead of shift in `SHOUT`?** A rotated byte is intact after
eight bits, so a repeated pattern (the 0x55 preamble) needs no reload and
loops stay tight.

**Why delay lines, and why so late?** They are the one thing here that
neither PIO nor a fixed peripheral can do: time below the clock. They were
added last because they are the riskiest part of the design for the tools
(section 13a) and needed a standalone experiment before touching the chip.
The first integrated version also taught an area lesson: 704 sample flops
took utilisation from 63 % to 70 % and the router could not close, so the
TDC registers its count instead of its samples.

**Why did the FIFO get added late?** The first Manchester demo used
immediates as payload and could not stream a frame. Adding a 128-byte FIFO
and a CRC-32 unit cost about 10% utilisation and made a real 64-byte frame
with a valid checksum possible; the layout still closes timing with
margin.

**What it deliberately does not do.** It does not drive an Ethernet cable
(that needs an external line driver, comparator and magnetics), it has no
receive-side CRC check in hardware (the host verifies), and frames longer
than 128 bytes would need the host to refill the FIFO faster than the link
allows.

## 20. Glossary

* **ASIC**: application-specific integrated circuit; a custom chip.
* **Combinational logic**: gates with no memory; outputs follow inputs
  immediately (`assign` statements, `wire`s).
* **Sequential logic**: flip-flops; state updated on a clock edge
  (`always @(posedge clk)`, `reg`s assigned with `<=`).
* **Synchroniser**: two flip-flops in series that turn an asynchronous
  input into a safe, one-clock-late signal.
* **Output enable (OE)**: the control that decides whether a bidirectional
  pin is driven or left floating.
* **Open drain**: a bus style where devices only pull low and a resistor
  pulls high; lets many devices share a wire.
* **PIO**: programmable I/O, the RP2040 name for tiny pin-bashing state
  machines.
* **FIFO**: first in, first out queue.
* **CRC / FCS**: cyclic redundancy check / frame check sequence, the
  checksum at the end of an Ethernet frame.
* **Manchester code**: each bit is a transition in the middle of the bit
  time, so the clock travels with the data.
* **Netlist**: the design expressed as cells and wires rather than RTL.
* **PDK**: process design kit, the foundry's cells, rules and models.
* **Utilisation**: fraction of the tile area covered by cells.
* **Setup / hold**: the timing rules a flip-flop input must obey around the
  clock edge.
* **DRC / LVS**: layout rule check / layout-versus-schematic comparison.
* **Formal verification**: proving a property for all inputs rather than
  testing some of them.
