import unittest
from tools.proto_asm import State, assemble, step


class AssemblerReferenceTest(unittest.TestCase):
    def test_gpio_program(self):
        state = State()
        for line in ("movi r0, 0xa5", "movi r1, 0xff", "write"):
            step(state, assemble(line))
        self.assertEqual((state.pins, state.oe), (0xA5, 0xFF))

    def test_jump_is_cycle_deterministic(self):
        state = State(pc=4)
        step(state, assemble("jmp 0x20"))
        self.assertEqual(state.pc, 0x20)

    def test_halt_releases_bus(self):
        state = State(oe=0xff)
        step(state, assemble("halt"))
        self.assertEqual(state.oe, 0)


if __name__ == "__main__":
    unittest.main()
