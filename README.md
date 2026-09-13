The challenge

Design an open-source, general-purpose protocol emulator ASIC.

Hardware protocols like UART, SPI, and I2C are simple enough that people routinely “bit-bang” them: toggle pins from software with careful timing instead of using a dedicated peripheral. A protocol emulator is a small chip built to do exactly that: a tiny CPU with an instruction set designed for reading pins, writing pins, counting cycles, and hitting timing precisely enough that you can implement a real protocol in firmware rather than in fixed logic. Something like that is a useful tool for hardware debugging and reverse engineering, which is a good part of what we do.

The hard part is flexibility. The goal isn’t to put a UART block, an SPI block, and an I2C block on one die and call it done. Your chip should be reprogrammable enough to support new protocols after fabrication, within its timing and I/O constraints. For inspiration, look at the PIO state machines on the RP2040 or the PRU cores on TI’s Sitara parts, and consider what you’d do differently.

Start with UART, SPI, and I2C.
Stretch goals include low-speed USB and 10Mbit Ethernet.
Other interesting protocols to consider: JTAG, SWD, PS/2, CAN bus
If you have access to an FPGA, consider using it to test your RTL before the ASIC flow.
Show us anything else your architecture makes possible that we haven’t thought of.
At Jane Street, we use Hardcaml to generate the RTL for our FPGA and ASIC designs. We are excited to see the languages and verification techniques you use, including formal methods, random constrained tests, AI-assisted verification, and more. As AI-assisted chip design becomes more common, we believe verification will be an extremely important aspect of the ASIC design flow going forwards.

The rules
Process: We’re targeting IHP’s 130nm CMOS5L process through our friends at Tiny Tapeout. Start with the CMOS5L Verilog template, which takes you from RTL to GDS. Set the tile size in info.yaml to 8x4.
Area: Our planned maximum is 8×4 Tiny Tapeout tiles per design.
Open source: Your submission should be open source so others can use and build on it. Unlike the reverse-engineering puzzle, there’s no need to keep your work hidden until the deadline, so feel free to build in public!
Teams: This is a much bigger project than the puzzle, so we strongly recommend working in teams.
Deadline: Submit your design by January 18th, 2027.
Prize: We’ll pay to tape out the most novel designs on a Tiny Tapeout shuttle. We’re targeting the March 2027 CMOS5L shuttle, subject to the foundry schedule. Winners will receive chips and dev boards back after fabrication, so you can test your design in silicon.
How much fits?
An 8x4 allocation is 32 tiles. At approximately 200um × 150um per tile, that’s about 1 mm² of nominal tile area. As a rough estimate, budget for about 1K logic cells per tile. You may need to get creative to fit the functionality you want.

For instruction memory, SRAM can be more area-efficient than flip-flops. Tiny Tapeout has examples of SRAM running on this process node you can reference.

Run synthesis early, check the mapped cell area, and leave room for clock-tree buffers and routing. Then run the full place-and-route flow and check timing. A design that looks small enough after synthesis can still be difficult to route or too slow at your chosen clock frequency.
