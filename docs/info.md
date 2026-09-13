<!---

This file is used to generate your project datasheet. Please fill in the information below and delete any unused
sections.

You can also include images in this folder and reference them in the markdown. Each image must be less than
512 kb in size, and the combined size of all images must be less than 1 MB.
-->

## How it works

This project is a programmable, general-purpose protocol emulator.  Its final
form has two deterministic PIO engines that execute small programs to sample
and drive GPIO pins with cycle-level timing.  The initial revision only brings
up the clock/reset path and keeps every bidirectional pin high impedance.

## How to test

Hold reset low, then release it. `uo[0]` becomes high after the first clock.
All `uio` pins remain inputs in this milestone.

## External hardware

The final design will use the Tiny Tapeout dev-board controller as a serial
programmer. Protocol targets may require external pull-ups, level translation,
or an Ethernet line-interface/magnetics daughterboard.
