"""Bring-up script for the Tiny Tapeout demo board (MicroPython, runs on the RP2040).

Copy tools/tt_board_host.py and this file to the board, select the project,
then run `import board_demo`.  It performs, in order:

1. ID and status readback over the host link.
2. Delay-line self-calibration: prints the stages per 12.5 ns on this die
   and the implied stage delay.
3. GPIO drive from the host (no program) so a scope or LED confirms pins.
4. Loads the UART transmitter and sends "TT\\n" at 2 Mbaud on GPIO0
   (change BAUD_FRAMES below for another rate).

The program words are the assembler's output for examples/uart_tx.pio
(`python tools/proto_asm.py examples/uart_tx.pio`); regenerate them if
that example changes.
"""
import time

from tt_board_host import ProtoHost

UART_TX_WORDS = [
    0x1401, 0x2401, 0x2501, 0x540D, 0x3200, 0x1808, 0xA000, 0x4011,
    0x0000, 0xA060, 0x4011, 0x7909, 0xA080, 0x4011, 0x6003,
]


def main(tt):
    h = ProtoHost(tt)
    h.reset()
    print("ID      0x%08x (expect 0x50494f31)" % h.read(4))
    print("STATUS  0x%08x" % h.read(0))

    stages = h.calibrate()
    print("calibration: %d stages per 12.5 ns -> %.0f ps per stage" % (stages, 12500 / max(stages, 1)))

    h.gpio(uio_oe=0xFF, uio_data=0xA5, uo_data=0x2A)
    print("host GPIO drive: uio=0xA5 uo=0x2A for 1 s")
    time.sleep(1)
    h.gpio(0, 0, 0)

    h.load(0, UART_TX_WORDS)
    h.run(start0=True)
    for ch in b"TT\n":
        h.mbox(0, ch)
        time.sleep_ms(1)
    print("UART: sent 'TT' + newline on GPIO0; STATUS 0x%08x" % h.read(0))
    return h


try:
    tt  # noqa: F821  (present on the demo board's REPL)
except NameError:
    print("run this on the Tiny Tapeout demo board where `tt` is defined")
else:
    main(tt)  # noqa: F821
