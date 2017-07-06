import unittest

from a816.expressions import eval_expr
from a816.symbols import Resolver


class ResolverTest(unittest.TestCase):
    def test_math_expr_eval(self):
        expr = '0x100+toto & 0xFFFF'

        resolver = Resolver()
        resolver.current_scope.add_symbol('toto', 0x108000)

        self.assertEqual(eval_expr(expr, resolver), 0x8100)

    def test_symbols_resolved_through_eval(self):
        expr = 'toto'
        resolver = Resolver()
        resolver.current_scope.add_symbol('toto', 0x1234)

        self.assertEqual(eval_expr(expr, resolver), 0x1234)

    def test_eval(self):
        r = Resolver()
        r.current_scope.add_symbol('name', {'data': 4})

        value = eval_expr('name.data', r)
        self.assertEqual(value, 4)
