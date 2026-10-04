import ctypes
import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from a816.error_codes import E_CODEGEN_DIVISION_BY_ZERO
from a816.exceptions import ExternalExpressionReference, ExternalSymbolReference, SymbolNotDefined
from a816.parse.ast.nodes import (
    BinOp,
    BlockAstNode,
    CastAccessExprNode,
    CastValueExprNode,
    ExpressionAstNode,
    ExprNode,
    Term,
    UnaryOp,
)
from a816.parse.tokens import Token, TokenType
from a816.symbols import Resolver

if TYPE_CHECKING:
    from a816.parse.nodes.errors import NodeError

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
        if isinstance(expr, Term | CastAccessExprNode | CastValueExprNode):
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


_NUMBER_BASES = {"0x": 16, "0b": 2, "0o": 8}


def eval_number(number: str) -> int:
    return int(number, _NUMBER_BASES.get(number[:2], 10))


def _truncating_div(a: int, b: int) -> int:
    """Integer division rounding toward zero (C / ca65), not Python's floor."""
    quotient = abs(a) // abs(b)
    return quotient if (a < 0) == (b < 0) else -quotient


def _truncating_mod(a: int, b: int) -> int:
    """Remainder matching `_truncating_div`: takes the sign of the dividend."""
    return a - b * _truncating_div(a, b)


_DIVISION_OPERATORS = frozenset({"/", "%"})

_INT_BINOPS: dict[str, Callable[[int, int], int]] = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "*": lambda a, b: a * b,
    "/": _truncating_div,
    "%": _truncating_mod,
    "&": lambda a, b: a & b,
    "|": lambda a, b: a | b,
    "^": lambda a, b: a ^ b,
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


def _division_by_zero(operator: Token) -> "NodeError":
    # Late import: a816.parse.nodes imports this module.
    from a816.parse.nodes.errors import NodeError

    return NodeError(
        "division by zero",
        operator,
        code=str(E_CODEGEN_DIVISION_BY_ZERO),
        hint=f"the right-hand side of `{operator.value}` evaluates to 0",
    )


def _apply_binary(operator: BinOp, v1: int | str, v2: int | str) -> int:
    op = operator.token.value
    if isinstance(v1, int) and isinstance(v2, int):
        if v2 == 0 and op in _DIVISION_OPERATORS:
            raise _division_by_zero(operator.token)
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


def _lookup(name: str, token: Token, resolver: Resolver) -> int | str | BlockAstNode | None:
    """Resolve `name`, tagging a miss with the term token that named it.

    An inner miss (e.g. through an alias expression) that already carries a
    located token keeps it: that is where the undefined name was written.
    """
    try:
        return resolver.current_scope.value_for(name)
    except SymbolNotDefined as exc:
        if exc.token is None or exc.token.position is None:
            exc.token = token
        raise


def _eval_inner(inner: list[ExprNode], resolver: Resolver) -> int | str:
    return eval_expression(ExpressionAstNode(list(inner)), resolver)


def _push_term(current: ExprNode, resolver: Resolver, values_stack: list[int | str]) -> None:
    if isinstance(current, CastAccessExprNode):
        base = _eval_inner(current.inner, resolver)
        if not isinstance(base, int):
            raise RuntimeError(f"Cast base does not evaluate to an address: {base!r}")  # noqa: TRY004 - invariant failure, not a caller type error
        field_symbol = ".".join([current.type_name, *current.field_path])
        offset = _lookup(field_symbol, current.leaf_token, resolver)
        if not isinstance(offset, int):
            raise RuntimeError(f"Struct field {field_symbol!r} did not resolve to an offset")  # noqa: TRY004 - invariant failure, not a caller type error
        values_stack.append(base + offset)
        return
    if isinstance(current, CastValueExprNode):
        base = _eval_inner(current.inner, resolver)
        if not isinstance(base, int):
            raise RuntimeError(f"Cast base does not evaluate to an address: {base!r}")  # noqa: TRY004 - invariant failure, not a caller type error
        values_stack.append(base)
        return
    if current.token.type == TokenType.NUMBER:
        values_stack.append(eval_number(current.token.value))
    elif current.token.type == TokenType.QUOTED_STRING:
        values_stack.append(current.token.value[1:-1])
    elif current.token.type == TokenType.IDENTIFIER:
        resolved_value = _lookup(current.token.value, current.token, resolver)
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
            # Inlining an alias like `OFF = lbl - base` surfaces the bare local
            # labels; canonicalize them to their exported `__sc<idx>__` form so
            # the relocation matches the linker's symbol map instead of folding
            # to 0 against unknown bare names.
            expression_str = _inline_aliases(reconstruct_expression(expression), resolver)
            expression_str = canonicalize_local_label_refs(expression_str, resolver)
            raise ExternalExpressionReference(expression_str, external_symbols)

    return _fold_rpn(ordered, lambda current, stack: _push_term(current, resolver, stack))


TermPusher = Callable[[ExprNode, list[int | str]], None]


def _fold_rpn(ordered: list[ExprNode], push_term: TermPusher) -> int | str:
    """Evaluate a shunting-yard output queue; `push_term` resolves operands."""
    values_stack: list[int | str] = []
    for current in ordered:
        if isinstance(current, UnaryOp):
            values_stack.append(_apply_unary(current.token.value, values_stack.pop()))
        elif isinstance(current, BinOp):
            v2 = values_stack.pop()
            v1 = values_stack.pop()
            values_stack.append(_apply_binary(current, v1, v2))
        else:
            push_term(current, values_stack)
    return values_stack.pop()


def _push_number(current: ExprNode, values_stack: list[int | str]) -> None:
    if current.token.type != TokenType.NUMBER:
        raise ValueError(f"cannot resolve `{current.token.value}` at link time")
    values_stack.append(eval_number(current.token.value))


def eval_constant_expression(expr_str: str) -> int:
    """Evaluate a symbol-free expression string with the assembler's semantics.

    The linker calls this once every symbol in a relocation expression has
    been substituted by its address. Raises `ValueError` for a leftover
    identifier, `ScannerException` / `ParserSyntaxError` for malformed text
    and `NodeError` for a division by zero.
    """
    ordered = shunting_yard(expr_to_ast(expr_str).tokens)
    return int(_fold_rpn(ordered, _push_number))


_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


def canonicalize_local_label_refs(expression_str: str, resolver: Resolver) -> str:
    """Rewrite local-label identifiers in `expression_str` to their
    EXPORTED form so an alias written into an `.o` (or a relocation
    expression) survives the linker's symbol-map lookup. Mirrors
    `ExpressionNode._compute_local_label_renames`: NamedScope members
    become `Name.label`; anon nested scopes get `__sc<idx>__name`.

    Use this anywhere an alias RHS is constructed from source text -
    `_register_alias`, macro-arg extern bindings, etc. - so the bare
    label name (`jt_label`) never reaches the object file with no
    matching export."""

    def replace(match: re.Match[str]) -> str:
        return resolver.exported_label_name(match.group(0))

    return _IDENT_RE.sub(replace, expression_str)


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
    from a816.parse.scanner_states import lex_standalone_expression

    scanner = Scanner(lex_standalone_expression)
    tokens = scanner.scan("memory", expr_str)
    if scanner.errors:
        raise scanner.errors[0]
    parser = Parser(tokens, parse_expression_ep)
    nodes = parser.parse()
    first_node = nodes[0]
    assert isinstance(first_node, ExpressionAstNode)
    return first_node


def eval_expression_str(expr_str: str, resolver: Resolver) -> int | str:
    expr_node = expr_to_ast(expr_str)
    return eval_expression(expr_node, resolver)
