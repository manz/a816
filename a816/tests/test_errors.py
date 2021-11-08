import unittest
from unittest.case import skip

from a816.parse.nodes import NodeError
from a816.program import Program
from a816.symbols import Resolver
from a816.tests import StubWriter
from a816.parse.ast.expression import eval_expression_str


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

    def test_eval_int(self) -> None:
        resolver = Resolver()
        resolver.current_scope.add_symbol("a", 4)
        value = eval_expression_str("a + 3", resolver)
        self.assertEqual(value, 4 + 3)

    @skip("BOOLEAN not really supported")
    def test_eval(self) -> None:
        resolver = Resolver()
        resolver.current_scope.add_symbol("a", 4)
        value = eval_expression_str("a != 3", resolver)
        self.assertEqual(value, True)

        resolver.current_scope.add_symbol("a", 3)
        value = eval_expression_str("a != 3", resolver)
        self.assertEqual(value, False)

        value = eval_expression_str("10 >= 1", resolver)
        self.assertEqual(value, True)

        value = eval_expression_str("1 > 1", resolver)
        self.assertEqual(value, False)

        value = eval_expression_str("0x2 < 0b100", resolver)
        self.assertEqual(value, True)
