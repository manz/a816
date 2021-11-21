import unittest
from unittest.case import skip

from a816.parse.ast.expression import eval_expression_str
from a816.parse.nodes import NodeError
from a816.program import Program
from a816.symbols import Resolver
from tests import StubWriter


class ErrorsTest(unittest.TestCase):
    def test_addressing_mode_error(self) -> None:
        program = Program()
        emitter = StubWriter()

        def should_raise_node_error() -> None:
            program.assemble_string_with_emitter("nop #0x00", "test.s", emitter)

        self.assertRaises(NodeError, should_raise_node_error)

    def test_opcode_size_error(self) -> None:
        program = Program()
        emitter = StubWriter()

        def should_raise_node_error() -> None:
            program.assemble_string_with_emitter("lda.l 0x000000, y\n", "test.s", emitter)

        self.assertRaises(NodeError, should_raise_node_error)

    def test_symbol_not_found(self) -> None:
        emitter = StubWriter()
        program = Program()
        try:
            program.assemble_string_with_emitter("jsr.l unknown_symbol\n", "test_undefined_symbol.s", emitter)
        except NodeError as e:
            v = str(e)
            self.assertEqual(
                v,
                """"unknown_symbol (ExpressionNode(unknown_symbol)) is not defined in the current scope." at 
test_undefined_symbol.s:0 jsr.l unknown_symbol""",
                "error should contain file information.",
            )

    def test_symbol_not_found_db(self) -> None:
        emitter = StubWriter()
        program = Program()
        try:
            program.assemble_string_with_emitter(".db unknown_symbol\n", "test_undefined_symbol_db.s", emitter)
        except NodeError as e:
            v = str(e)
            self.assertEqual(
                v,
                """"unknown_symbol (ExpressionNode(unknown_symbol)) is not defined in the current scope." at 
test_undefined_symbol_db.s:0 .db unknown_symbol""",
                "error should contain file information.",
            )
