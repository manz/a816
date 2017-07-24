import unittest

from a816.parse.nodes import NodeError
from a816.program import Program
from a816.tests import StubWriter


class ErrorsTest(unittest.TestCase):
    def test_addressing_mode_error(self):
        program = Program()
        emitter = StubWriter()

        def should_raise_node_error():
            program.assemble_string_with_emitter('nop #0x00', 'test.s', emitter)

        self.assertRaises(NodeError, should_raise_node_error)

    def test_opcode_size_error(self):
        program = Program()
        emitter = StubWriter()

        def should_raise_node_error():
            program.assemble_string_with_emitter('lda.l 0x000000, y\n', 'test.s', emitter)

        self.assertRaises(NodeError, should_raise_node_error)

    def test_undefined_symbol(self):
        program = Program()
        emitter = StubWriter()

        def should_raise_node_error():
            program.assemble_string_with_emitter('lda.l undefined_symbol', 'test.s', emitter)

        self.assertRaises(NodeError, should_raise_node_error)
