import unittest
from unittest import TestCase, skip

from a816.parse.ast.expression import eval_expression_str
from a816.parse.errors import ParserSyntaxError
from a816.symbols import Resolver


class ExpressionsTest(TestCase):
    def test_bogus_expression(self) -> None:
        expr = "0x100 + + 0xFFFF"

        resolver = Resolver()

        with self.assertRaises(ParserSyntaxError):
            eval_expression_str(expr, resolver)

    def test_math_expr_eval(self) -> None:
        expr = "0x100+toto & 0xFFFF"

        resolver = Resolver()
        resolver.current_scope.add_symbol("toto", 0x108000)

        self.assertEqual(eval_expression_str(expr, resolver), 0x8100)

    def test_unary(self) -> None:
        r = Resolver()
        value = eval_expression_str("-1", r)
        self.assertEqual(value, -1)

    def test_eval_int(self) -> None:
        resolver = Resolver()
        resolver.current_scope.add_symbol("a", 4)
        value = eval_expression_str("a + 3", resolver)
        self.assertEqual(value, 4 + 3)

    def test_plus(self) -> None:
        resolver = Resolver()
        value = eval_expression_str("2 + 3", resolver)
        self.assertEqual(value, 2 + 3)

    def test_minus(self) -> None:
        resolver = Resolver()
        value = eval_expression_str("3 - 3", resolver)
        self.assertEqual(value, 0)

    def test_or(self) -> None:
        resolver = Resolver()

        value = eval_expression_str("0x00ff | 0xff00", resolver)
        self.assertEqual(0x00FF | 0xFF00, value)

    def test_lshift(self) -> None:
        resolver = Resolver()

        value = eval_expression_str("1 << 16", resolver)
        self.assertEqual(1 << 16, value)

    def test_rshift(self) -> None:
        resolver = Resolver()

        value = eval_expression_str("1 >> 16", resolver)
        self.assertEqual(1 >> 16, value)

    def test_parenthesis(self) -> None:
        resolver = Resolver()

        value = eval_expression_str("(1 + 3) * 5", resolver)
        self.assertEqual((1 + 3) * 5, value)

    @skip("BOOLEAN not really supported")
    def test_eval_bool(self) -> None:
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
