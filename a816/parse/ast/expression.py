import ctypes
import re
from collections.abc import Callable

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


def _pop_higher_precedence(
    operator_stack: list[ExprNode], output_queue: list[ExprNode], current_precedence: int
) -> None:
    while (
        operator_stack
        and OPERATOR_PRECEDENCE[operator_stack[-1].token.value] <= current_precedence
        and operator_stack[-1].token.value != "("
    ):
        output_queue.append(operator_stack.pop())


def _pop_until_lparen(operator_stack: list[ExprNode], output_queue: list[ExprNode]) -> None:
    lparen_index = reverse_find_token(operator_stack, "(")
    if lparen_index < 0:
        raise ValueError("mismatched parenthesis")
    while len(operator_stack) > lparen_index + 1:
        output_queue.append(operator_stack.pop())
    operator_stack.pop()


def shunting_yard(expr_nodes: list[ExprNode]) -> list[ExprNode]:
    output_queue: list[ExprNode] = []
    operator_stack: list[ExprNode] = []

    for expr in expr_nodes:
        if isinstance(expr, Term):
            output_queue.append(expr)
        elif isinstance(expr, BinOp | UnaryOp):
            current_precedence = OPERATOR_PRECEDENCE[expr.token.value] if isinstance(expr, BinOp) else 2
            _pop_higher_precedence(operator_stack, output_queue, current_precedence)
            operator_stack.append(expr)
        elif expr.token.type == TokenType.LPAREN:
            operator_stack.append(expr)
        elif expr.token.type == TokenType.RPAREN:
            _pop_until_lparen(operator_stack, output_queue)

    while operator_stack:
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


_INT_BINOPS: dict[str, Callable[[int, int], int]] = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "&": lambda a, b: a & b,
    "|": lambda a, b: a | b,
    ">>": lambda a, b: a >> b,
    "<<": lambda a, b: a << b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}

_STR_BINOPS: dict[str, Callable[[str, str], int]] = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def _bitwise_not(value: int) -> int:
    if value.bit_length() <= 8:
        return ctypes.c_uint8(~value).value
    if value.bit_length() <= 16:
        return ctypes.c_uint16(~value).value
    if value.bit_length() <= 32:
        return ctypes.c_uint32(~value).value
    raise RuntimeError("not only works up 32 bits integers.")


def _apply_unary(op: str, value: int | str) -> int:
    assert isinstance(value, int)
    if op == "-":
        return -value
    if op == "~":
        return _bitwise_not(value)
    raise RuntimeError(f"Unsupported unary Operator {op}")


def _apply_binary(op: str, v1: int | str, v2: int | str) -> int:
    if isinstance(v1, int) and isinstance(v2, int):
        try:
            return _INT_BINOPS[op](v1, v2)
        except KeyError as e:
            raise RuntimeError("operator unknown") from e
    if isinstance(v1, str) and isinstance(v2, str):
        try:
            return _STR_BINOPS[op](v1, v2)
        except KeyError as e:
            raise RuntimeError("operator unknown") from e
    raise RuntimeError("Mismatched types in expression")


def _collect_external_symbols(ordered: list[ExprNode], resolver: Resolver) -> set[str]:
    external_symbols: set[str] = set()
    for current in ordered:
        if current.token.type != TokenType.IDENTIFIER:
            continue
        try:
            resolver.current_scope.value_for(current.token.value)
        except ExternalSymbolReference as e:
            external_symbols.add(e.symbol_name)
    return external_symbols


def _push_term(current: ExprNode, resolver: Resolver, values_stack: list[int | str]) -> None:
    if current.token.type == TokenType.NUMBER:
        values_stack.append(eval_number(current.token.value))
    elif current.token.type == TokenType.QUOTED_STRING:
        values_stack.append(current.token.value[1:-1])
    elif current.token.type == TokenType.IDENTIFIER:
        resolved_value = resolver.current_scope.value_for(current.token.value)
        if not isinstance(resolved_value, int | str):
            raise RuntimeError(f"Unable  to resolve {current.token.value}")
        values_stack.append(resolved_value)


def eval_expression(expression: ExpressionAstNode, resolver: Resolver) -> int | str:
    """Evaluate an expression, detecting external symbol references"""
    ordered = shunting_yard(expression.tokens)

    if resolver.context.is_object_mode:
        external_symbols = _collect_external_symbols(ordered, resolver)
        if external_symbols:
            # Reconstruct + inline aliases so the relocation references real
            # externs (macro-arg bindings otherwise leak into the object file).
            expression_str = _inline_aliases(reconstruct_expression(expression), resolver)
            raise ExternalExpressionReference(expression_str, external_symbols)

    values_stack: list[int | str] = []
    for current in ordered:
        if isinstance(current, UnaryOp):
            values_stack.append(_apply_unary(current.token.value, values_stack.pop()))
        elif isinstance(current, BinOp):
            v2 = values_stack.pop()
            v1 = values_stack.pop()
            values_stack.append(_apply_binary(current.token.value, v1, v2))
        else:
            _push_term(current, resolver, values_stack)
    return values_stack.pop()


_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


def _inline_aliases(expression_str: str, resolver: Resolver, depth: int = 0) -> str:
    """Replace alias names in ``expression_str`` with their underlying expressions.

    Macro-arg aliases bind a name to an expression that references real
    externs; inlining keeps the relocation expression independent of the
    transient scope where the alias lived.
    """
    if depth > 16:
        return expression_str  # bail on suspected cycle

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        alias_expr = resolver.current_scope.lookup_alias(token)
        if alias_expr is None or alias_expr == token:
            return token
        return "(" + _inline_aliases(alias_expr, resolver, depth + 1) + ")"

    return _IDENT_RE.sub(replace, expression_str)


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
