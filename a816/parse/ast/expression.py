import ctypes

from a816.exceptions import ExternalExpressionReference, ExternalSymbolReference
from a816.parse.ast.nodes import BinOp, ExpressionAstNode, ExprNode, Term, UnaryOp
from a816.parse.tokens import TokenType
from a816.symbols import Resolver

OPERATOR_PRECEDENCE = {
    # unary 1
    "(": 1,
    ")": 1,
    "~": 2,
    "*": 3,
    "/": 3,
    "%": 3,
    "+": 4,
    "-": 4,
    "<<": 5,
    ">>": 5,
    ">=": 6,
    "<=": 6,
    ">": 6,
    "<": 6,
    "==": 7,
    "!=": 7,
    "&": 8,
    "^": 9,
    "|": 10,
}


def reverse_find_token(items: list[ExprNode], value: str) -> int:
    for pos in range(len(items) - 1, -1, -1):
        if items[pos].token.value == value:
            return pos
    return -1


def shunting_yard(expr_nodes: list[ExprNode]) -> list[ExprNode]:
    output_queue: list[ExprNode] = []
    operator_stack: list[ExprNode] = []

    for expr in expr_nodes:
        if isinstance(expr, Term):
            output_queue.append(expr)
        elif isinstance(expr, BinOp) or isinstance(expr, UnaryOp):
            current_precedence = OPERATOR_PRECEDENCE[expr.token.value] if isinstance(expr, BinOp) else 2

            while (
                len(operator_stack) > 0
                and OPERATOR_PRECEDENCE[operator_stack[-1].token.value] <= current_precedence
                and operator_stack[-1].token.value != "("
            ):
                output_queue.append(operator_stack.pop())
            operator_stack.append(expr)
        elif expr.token.type == TokenType.LPAREN:
            operator_stack.append(expr)
        elif expr.token.type == TokenType.RPAREN:
            lparen_index = reverse_find_token(operator_stack, "(")

            if lparen_index < 0:
                raise ValueError("mismatched parenthesis")

            while len(operator_stack) > lparen_index + 1:
                op = operator_stack.pop()
                output_queue.append(op)
            operator_stack.pop()

    while len(operator_stack) > 0:
        output_queue.append(operator_stack.pop())

    return output_queue


def eval_number(number: str) -> int:
    if number.startswith("0x"):
        base = 16
    elif number.startswith("0b"):
        base = 2
    else:
        base = 10

    return int(number, base)


def eval_expression(expression: ExpressionAstNode, resolver: Resolver) -> int | str:
    """Evaluate an expression, detecting external symbol references"""
    tokens = expression.tokens
    ordered = shunting_yard(tokens)

    # First pass: collect external symbols referenced in this expression
    external_symbols: set[str] = set()

    # Check if we're compiling to an object file and need to defer external expressions
    if resolver.context.is_object_mode:
        for current in ordered:
            if current.token.type == TokenType.IDENTIFIER:
                try:
                    resolver.current_scope.value_for(current.token.value)
                except ExternalSymbolReference as e:
                    external_symbols.add(e.symbol_name)

        # If expression contains external symbols, throw exception to defer evaluation
        if external_symbols:
            # Reconstruct expression string from tokens
            expression_str = reconstruct_expression(expression)
            raise ExternalExpressionReference(expression_str, external_symbols)

    values_stack: list[int | str] = []
    r: int

    for current in ordered:
        if current.token.type == TokenType.NUMBER:
            values_stack.append(eval_number(current.token.value))
        elif current.token.type == TokenType.QUOTED_STRING:
            values_stack.append(current.token.value[1:-1])
        elif current.token.type == TokenType.IDENTIFIER:
            resolved_value = resolver.current_scope.value_for(current.token.value)
            if isinstance(resolved_value, int) or isinstance(resolved_value, str):
                values_stack.append(resolved_value)
            else:
                raise RuntimeError(f"Unable  to resolve {current.token.value}")
        elif isinstance(current, UnaryOp):
            v1 = values_stack.pop()
            assert isinstance(v1, int)
            if current.token.value == "-":
                assert isinstance(v1, int)
                r = -v1
            elif current.token.value == "~":
                if v1.bit_length() <= 8:
                    r = ctypes.c_uint8(~v1).value
                elif v1.bit_length() <= 16:
                    r = ctypes.c_uint16(~v1).value
                elif v1.bit_length() <= 32:
                    r = ctypes.c_uint32(~v1).value
                else:
                    raise RuntimeError("not only works up 32 bits integers.")
            else:
                raise RuntimeError(f"Unsupported unary Operator {current.token}")

            values_stack.append(r)
        elif isinstance(current, BinOp):
            v2 = values_stack.pop()
            v1 = values_stack.pop()
            if isinstance(v1, int) and isinstance(v2, int):
                if current.token.value == "+":
                    r = v1 + v2
                elif current.token.value == "-":
                    r = v1 - v2
                elif current.token.value == "*":
                    r = v1 * v2
                elif current.token.value == "&":
                    r = v1 & v2
                elif current.token.value == "|":
                    r = v1 | v2
                elif current.token.value == ">>":
                    r = v1 >> v2
                elif current.token.value == "<<":
                    r = v1 << v2
                elif current.token.value == ">=":
                    r = v1 >= v2
                elif current.token.value == "<=":
                    r = v1 <= v2
                elif current.token.value == "<":
                    r = v1 < v2
                elif current.token.value == ">":
                    r = v1 > v2
                elif current.token.value == "==":
                    r = v1 == v2
                elif current.token.value == "!=":
                    r = v1 != v2
                else:
                    raise RuntimeError("operator unknown")
            elif isinstance(v1, str) and isinstance(v2, str):
                if current.token.value == "==":
                    r = v1 == v2
                elif current.token.value == "!=":
                    r = v1 != v2
                else:
                    raise RuntimeError("operator unknown")
            else:
                raise RuntimeError("Mismatched types in expression")
            values_stack.append(r)
    return values_stack.pop()


def reconstruct_expression(expression: ExpressionAstNode) -> str:
    """Reconstruct the original expression string from the AST"""
    tokens = expression.tokens
    result = []

    for token in tokens:
        if hasattr(token, "token"):
            result.append(token.token.value)
        else:
            result.append(str(token))

    return " ".join(result)


def expr_to_ast(expr_str: str) -> ExpressionAstNode:
    from a816.parse.parser import Parser
    from a816.parse.parser_states import parse_expression_ep
    from a816.parse.scanner import Scanner
    from a816.parse.scanner_states import lex_expression

    scanner = Scanner(lex_expression)
    tokens = scanner.scan("memory", expr_str)
    parser = Parser(tokens, parse_expression_ep)
    nodes = parser.parse()
    first_node = nodes[0]
    assert isinstance(first_node, ExpressionAstNode)
    return first_node


def eval_expression_str(expr_str: str, resolver: Resolver) -> int | str:
    expr_node = expr_to_ast(expr_str)
    return eval_expression(expr_node, resolver)
