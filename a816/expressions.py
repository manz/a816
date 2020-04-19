import ast
import operator as op

# supported operators

operators = {ast.Add: op.add,
             ast.Sub: op.sub,
             ast.Mult: op.mul,
             ast.Pow: op.pow,
             ast.BitXor: op.xor,
             ast.LShift: op.lshift,
             ast.RShift: op.rshift,
             ast.BitAnd: op.iand,
             ast.UAdd: op.pos,
             ast.USub: op.neg}


def expr_to_str(expr):
    if isinstance(expr, tuple):
        return '(' + expr_to_str(expr[1]) + expr[0] + expr_to_str(expr[2]) + ')'
    else:
        return expr


def eval_expr(expr, resolver):
    expr = expr_to_str(expr)
    expr = expr.replace('$', '0x')

    def eval_(node):
        if isinstance(node, ast.Num):  # <number>
            return node.n
        if isinstance(node, ast.Name):
            return resolver.current_scope.value_for(node.id)
        elif isinstance(node, ast.operator):  # <operator>
            return operators[type(node)]
        elif isinstance(node, ast.BinOp):  # <left> <operator> <right>
            return eval_(node.op)(eval_(node.left), eval_(node.right))
        elif isinstance(node, ast.UnaryOp):  # <unary_op> <expr>
            return operators[type(node.op)](eval_(node.operand))
        elif isinstance(node, ast.Attribute):
            named_scope = resolver.current_scope.value_for(node.value.id)
            return named_scope[node.attr]
        else:
            raise TypeError(node)

    py_ast = ast.parse(expr).body[0].value
    return eval_(py_ast)
