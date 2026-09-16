"""Unit tests for the assembler, disassembler and reference model (pytest)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
from proto_asm import AsmError, assemble, assemble_program, disassemble, program_frames  # noqa: E402
from proto_ref import Chip, Command, Engine, cmd_program, cmd_run  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "examples")


class AssemblerTest(unittest.TestCase):
    def test_round_trip_every_opcode(self):
        lines = ["nop", "movi r2, 0x7f", "out r1, UIO_OE, 0x0f", "in r3, MBOX", "delay 200",
                 "delay r1", "delay 12*16", "wait RISE, GPIO5", "jmp 0x10", "djnz r0, 0x03",
                 "jph TRIG, 0x22", "jpl IN2, 0x01", "setp UO, 6, 1", "setp UIO_OE, 2, C",
                 "shout_msb r0, UO, 0, INV", "shout_lsb r2, UIO, 7", "shl r1", "shin_lsb r0, IN0",
                 "add r0, r1", "movc r3", "not r2", "crcu r1", "crci", "crcb r2, 3", "pop r0", "in r0, TDC0", "in r1, TDCLVL", "dtcw r3, 1", "trace 0x42", "trace r1", "mbox r0", "done", "halt"]
        for line in lines:
            word = assemble(line)
            self.assertEqual(assemble(disassemble(word)), word, line)

    def test_labels_and_symbols(self):
        prog = assemble_program(".equ N 3\nstart: movi r0, N\nloop: djnz r0, loop\njmp start\n")
        self.assertEqual(prog.words, [0x1003, 0x7101, 0x6000])
        self.assertEqual(prog.labels, {"start": 0, "loop": 1})

    def test_errors(self):
        with self.assertRaises(AsmError):
            assemble_program("movi r4, 1")
        with self.assertRaises(AsmError):
            assemble_program("movi r0, 256")
        with self.assertRaises(AsmError):
            assemble_program("jmp nowhere")

    def test_examples_assemble_and_fit(self):
        for name in sorted(os.listdir(EXAMPLES)):
            prog = assemble_program(open(os.path.join(EXAMPLES, name)).read())
            self.assertLessEqual(len(prog.words), 128, name)

    def test_program_frames(self):
        self.assertEqual(program_frames([0x1234], engine=1, base=5),
                         [(2 << 28) | (1 << 27) | (5 << 19) | (0x1234 << 3)])


class ReferenceModelTest(unittest.TestCase):
    def run_program(self, text, cycles=40, engine=0):
        chip = Chip()
        for i, w in enumerate(assemble_program(text).words):
            chip.imem[engine][i] = w
        chip.step(0, 0, Command(cmd_run(start0=engine == 0, start1=engine == 1)))
        for _ in range(cycles):
            chip.step(0, 0)
        return chip

    def test_write_and_halt_release(self):
        chip = self.run_program("movi r0, 0xa5\nmovi r1, 0xff\nout r1, UIO_OE\nout r0, UIO\ndelay 3\nhalt")
        self.assertFalse(chip.eng[0].running)
        self.assertEqual(chip.uio_oe, 0)
        self.assertEqual(chip.eng[0].out.uio_data, 0xA5)

    def test_rotate_shout_restores_register(self):
        chip = self.run_program("movi r0, 0x96\nmovi r1, 8\nl: shout_msb r0, UO, 0\ndjnz r1, l\nhalt")
        self.assertEqual(chip.eng[0].regs[0], 0x96)

    def test_collision_is_high_impedance(self):
        chip = Chip()
        for g in range(2):
            for i, w in enumerate(assemble_program("movi r0, 1\nout r0, UIO_OE\nl: jmp l").words):
                chip.imem[g][i] = w
        chip.step(0, 0, Command(cmd_run(start0=True, start1=True)))
        for _ in range(10):
            chip.step(0, 0)
        self.assertEqual(chip.uio_oe, 0)
        self.assertTrue(chip.fault_collision)

    def test_fifo_fcs_matches_zlib(self):
        import zlib
        from proto_ref import cmd_fifo
        chip = Chip()
        data = list(range(1, 61))
        chip.step(0, 0, Command(cmd_fifo(reset=True, fcs_mode=True)))
        for b in data:
            chip.step(0, 0, Command(cmd_fifo(b, push=True, fcs_mode=True)))
        chip.step(0, 0)
        for i, w in enumerate(assemble_program("l: pop r0\nmbox r0\njmp l").words):
            chip.imem[0][i] = w
        chip.step(0, 0, Command(cmd_run(start0=True)))
        out = []
        for _ in range(3 * 66 + 4):
            chip.step(0, 0)
            if chip.eng[0].pc == 1 and chip.eng[0].carry:
                out.append(chip.eng[0].regs[0])
        fcs = zlib.crc32(bytes(data)) & 0xFFFFFFFF
        self.assertEqual(out, data + [(fcs >> (8 * k)) & 0xFF for k in range(4)])

    def test_delay_is_deterministic(self):
        eng = Engine()
        eng.step(0, 0, 0, 0, True, 0, False, False)
        eng.step(0x4003, 0, 0, 0, False, 0, False, False)  # delay 3
        self.assertEqual(eng.delay, 3)
        for _ in range(3):
            eng.step(0xE000, 0, 0, 0, False, 0, False, False)
        self.assertTrue(eng.running)
        eng.step(0xE000, 0, 0, 0, False, 0, False, False)
        self.assertFalse(eng.running)


if __name__ == "__main__":
    unittest.main()
