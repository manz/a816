import unittest

from a816.parse.ast.expression import eval_expression_str
from a816.symbols import Resolver


class ResolverTest(unittest.TestCase):
    def test_math_expr_eval(self) -> None:
        expr = "0x100+toto & 0xFFFF"

        resolver = Resolver()
        resolver.current_scope.add_symbol("toto", 0x108000)

        self.assertEqual(eval_expression_str(expr, resolver), 0x8100)

    def test_symbols_resolved_through_eval(self) -> None:
        expr = "toto"
        resolver = Resolver()
        resolver.current_scope.add_symbol("toto", 0x1234)

        self.assertEqual(eval_expression_str(expr, resolver), 0x1234)

    def test_eval(self) -> None:
        r = Resolver()
        r.current_scope.add_symbol("name.data", 4)

        value = eval_expression_str("name.data", r)
        self.assertEqual(value, 4)

    def test_unary(self) -> None:
        r = Resolver()
        value = eval_expression_str("-1", r)
        self.assertEqual(value, -1)
