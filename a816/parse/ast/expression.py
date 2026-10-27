import ctypes
from collections.abc import Callable, Sequence

from a816.error_codes import (
    E_CODEGEN_DIVISION_BY_ZERO,
    E_CODEGEN_MISMATCHED_TYPES,
    E_CODEGEN_NOT_TOO_WIDE,
    E_CODEGEN_TYPED_BIND_NON_INT,
    E_SYMBOL_NOT_A_VALUE,
    ErrorCode,
)
from a816.exceptions import A816Error, ExternalExpressionReference, ExternalSymbolReference, SymbolNotDefined
from a816.link_expr import ExprOp, LinkExpr, binary_op, constant, symbol, term_op, unary_op
from a816.parse.ast.nodes import (
    BinOp,
    BlockAstNode,
    CastAccessExprNode,
    CastValueExprNode,
    ExpressionAstNode,
    ExprNode,
    SizeofExprNode,
    Term,
    UnaryOp,
)
from a816.parse.ast.nodes.base import shunting_yard
from a816.parse.tokens import Token, TokenType
from a816.symbols import Resolver


def _ordered(expression: ExpressionAstNode) -> list[ExprNode] | tuple[ExprNode, ...]:
    """The expression in evaluation order: the RPN its node worked out at
    parse time, or a fresh ordering that reports its mismatched parenthesis."""
    return expression.rpn if expression.rpn is not None else shunting_yard(expression.tokens)


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


def _expression_error(message: str, token: Token, code: ErrorCode, hint: str | None = None) -> A816Error:
    """Located diagnostic for a user mistake found while evaluating."""
    # Late import: `a816.parse.nodes` imports this module, so a top-level
    # import of NodeError would be circular.
    from a816.parse.nodes.errors import NodeError

    return NodeError(message, token, code=str(code), hint=hint)


class ExprEvalError(Exception):
    """An evaluation mistake with no source site: codegen locates it on its
    token, the linker on the relocation's line."""

    def __init__(self, message: str, code: ErrorCode, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.hint = hint


def bitwise_not(value: int) -> int:
    """`~value` masked to the narrowest of 8, 16 or 32 bits that holds it."""
    if value.bit_length() <= 8:
        return ctypes.c_uint8(~value).value
    if value.bit_length() <= 16:
        return ctypes.c_uint16(~value).value
    if value.bit_length() <= 32:
        return ctypes.c_uint32(~value).value
    raise ExprEvalError(f"`~` operand {value:#x} is wider than 32 bits", E_CODEGEN_NOT_TOO_WIDE)


def unary_value(op: str, value: int | str) -> int:
    """The assembler's unary `op` applied to `value`."""
    if not isinstance(value, int):
        raise ExprEvalError(f"`{op}` cannot apply to a string", E_CODEGEN_MISMATCHED_TYPES)
    if op == "-":
        return -value
    if op == "~":
        return bitwise_not(value)
    if op == "<":
        return value & 0xFF
    if op == ">":
        return (value >> 8) & 0xFF
    raise RuntimeError(f"Unsupported unary Operator {op}")


def binary_value(op: str, v1: int | str, v2: int | str) -> int:
    """The assembler's binary `op` applied to `v1` and `v2`; a comparison is 0 or 1."""
    if isinstance(v1, int) and isinstance(v2, int):
        if v2 == 0 and op in _DIVISION_OPERATORS:
            raise ExprEvalError(
                "division by zero", E_CODEGEN_DIVISION_BY_ZERO, hint=f"the right-hand side of `{op}` evaluates to 0"
            )
        try:
            return int(_INT_BINOPS[op](v1, v2))
        except KeyError as e:
            raise RuntimeError("operator unknown") from e
    if isinstance(v1, str) and isinstance(v2, str):
        try:
            return int(_STR_BINOPS[op](v1, v2))
        except KeyError as e:
            raise RuntimeError("operator unknown") from e
    raise ExprEvalError(
        f"`{op}` cannot combine {type(v1).__name__} and {type(v2).__name__}", E_CODEGEN_MISMATCHED_TYPES
    )


def _located(error: ExprEvalError, token: Token) -> A816Error:
    return _expression_error(error.message, token, error.code, error.hint)


def _apply_unary(op: str, value: int | str, token: Token) -> int:
    try:
        return unary_value(op, value)
    except ExprEvalError as error:
        raise _located(error, token) from None


def _apply_binary(operator: BinOp, v1: int | str, v2: int | str) -> int:
    try:
        return binary_value(operator.token.value, v1, v2)
    except ExprEvalError as error:
        raise _located(error, operator.token) from None


def _collect_external_symbols(ordered: Sequence[ExprNode], resolver: Resolver) -> set[str]:
    external_symbols: set[str] = set()
    for current in ordered:
        if isinstance(current, CastAccessExprNode | CastValueExprNode):
            external_symbols |= _collect_external_symbols(current.inner, resolver)
            continue
        if isinstance(current, SizeofExprNode):
            external_symbols |= _external_size(current, resolver)
            continue
        if current.token.type != TokenType.IDENTIFIER:
            continue
        try:
            _lookup(current.token.value, current.token, resolver)
        except ExternalSymbolReference as e:
            external_symbols.add(e.symbol_name)
    return external_symbols


def _external_size(node: SizeofExprNode, resolver: Resolver) -> set[str]:
    """The `NAME.__size` symbol of an imported reservation, which the linker resolves."""
    from a816.parse.ast.size_of import size_of

    size = size_of(node, resolver)
    if isinstance(size, int):
        return set()
    try:
        _lookup(size, node.path_token, resolver)
    except ExternalSymbolReference as e:
        return {e.symbol_name}
    return set()


def _lookup(name: str, token: Token, resolver: Resolver) -> int | str | BlockAstNode | None:
    """Resolve `name`, tagging a miss with the term token that named it.

    An inner miss (e.g. through an alias expression) that already carries a
    located token keeps it: that is where the undefined name was written.
    """
    try:
        value = resolver.current_scope.value_for(name)
    except SymbolNotDefined as exc:
        if exc.name == name:
            _raise_for_macro_argument(name, token, resolver)
        if exc.token is None or exc.token.position is None:
            exc.token = token
        raise
    owner = resolver.foreign_private_owner(name, token)
    if owner is not None:
        hidden = SymbolNotDefined(name, token)
        hidden.note = private_hint(name, owner)
        raise hidden
    return value


def private_hint(name: str, module: str) -> str:
    return f"`{name}` is private to module `{module}`; drop the leading `_` there to export it"


def _raise_for_macro_argument(name: str, use: Token, resolver: Resolver) -> None:
    """A miss on a macro parameter whose argument never resolved: report
    what the argument names, where the caller wrote it, and note where the
    macro body used it."""
    pending = resolver.current_scope.macro_argument(name)
    if pending is None:
        return
    argument, note = pending
    position = use.position
    if position is not None and position.file is not None:
        note = f"{note}, used at {position.file.filename}:{position.line + 1}"
    try:
        eval_expression(argument, resolver)
    except SymbolNotDefined as inner:
        inner.note = inner.note or note
        raise


def _eval_inner(inner: Sequence[ExprNode], resolver: Resolver) -> int | str:
    return eval_expression(ExpressionAstNode(list(inner)), resolver)


def _eval_cast_base(current: CastAccessExprNode | CastValueExprNode, resolver: Resolver) -> int:
    """Evaluate `inner` of `(inner as T)`; it must be an address."""
    base = _eval_inner(current.inner, resolver)
    if not isinstance(base, int):
        where = current.inner[0].token if current.inner else current.token
        raise _expression_error(
            f"cast base does not evaluate to an address: {base!r}", where, E_CODEGEN_TYPED_BIND_NON_INT
        )
    return base


def _field_offset(cast: CastAccessExprNode, resolver: Resolver) -> int:
    """Byte offset of `(inner as T).a.b` within `T`."""
    field_symbol = ".".join([cast.type_name, *cast.field_path])
    offset = _lookup(field_symbol, cast.leaf_token, resolver)
    if not isinstance(offset, int):
        raise RuntimeError(f"Struct field {field_symbol!r} did not resolve to an offset")  # noqa: TRY004 - invariant failure, not a caller type error
    return offset


def _size_value(node: SizeofExprNode, resolver: Resolver) -> int:
    """`sizeof` / `countof` as a number; a size only a symbol carries is looked up."""
    from a816.parse.ast.size_of import size_of

    size = size_of(node, resolver)
    if isinstance(size, int):
        return size
    value = _lookup(size, node.path_token, resolver)
    if not isinstance(value, int):
        raise RuntimeError(f"{size!r} did not resolve to a size")  # noqa: TRY004 - invariant failure
    return value


def _push_term(current: ExprNode, resolver: Resolver, values_stack: list[int | str]) -> None:
    if isinstance(current, SizeofExprNode):
        values_stack.append(_size_value(current, resolver))
        return
    if isinstance(current, CastAccessExprNode):
        values_stack.append(_eval_cast_base(current, resolver) + _field_offset(current, resolver))
        return
    if isinstance(current, CastValueExprNode):
        values_stack.append(_eval_cast_base(current, resolver))
        return
    if current.token.type == TokenType.NUMBER:
        values_stack.append(eval_number(current.token.value))
    elif current.token.type == TokenType.QUOTED_STRING:
        values_stack.append(current.token.value[1:-1])
    elif current.token.type == TokenType.IDENTIFIER:
        resolved_value = _lookup(current.token.value, current.token, resolver)
        if not isinstance(resolved_value, int | str):
            raise _expression_error(
                f"`{current.token.value}` names a block, not a value", current.token, E_SYMBOL_NOT_A_VALUE
            )
        values_stack.append(resolved_value)


def eval_expression(expression: ExpressionAstNode, resolver: Resolver) -> int | str:
    """Evaluate an expression, detecting external symbol references"""
    ordered = _ordered(expression)

    if resolver.context.is_object_mode:
        external_symbols = _collect_external_symbols(ordered, resolver)
        if external_symbols:
            # Aliases inline (a macro argument's name means nothing outside
            # the call) and labels take their exported names, so the linker
            # finds every name the relocation reads.
            raise ExternalExpressionReference(link_expression(expression, resolver), external_symbols)

    return _fold_rpn(ordered, lambda current, stack: _push_term(current, resolver, stack))


TermPusher = Callable[[ExprNode, list[int | str]], None]


def _fold_rpn(ordered: Sequence[ExprNode], push_term: TermPusher) -> int | str:
    """Evaluate a shunting-yard output queue; `push_term` resolves operands."""
    values_stack: list[int | str] = []
    for current in ordered:
        if isinstance(current, UnaryOp):
            values_stack.append(_apply_unary(current.token.value, values_stack.pop(), current.token))
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
    and `NodeError` for a division by zero or an over-wide `~` operand.
    """
    ordered = _ordered(expr_to_ast(expr_str))
    return int(_fold_rpn(ordered, _push_number))


def link_expression(expression: ExpressionAstNode, resolver: Resolver) -> LinkExpr:
    """The expression as the linker evaluates it: casts lowered to
    arithmetic, sizes folded, aliases inlined, and every other name in the
    form the object exports it (see `link_name`)."""
    return resolve_link_names(lower_expression(expression, resolver), resolver)


def lower_expression(expression: ExpressionAstNode, resolver: Resolver) -> LinkExpr:
    """The expression's ops with its names as written. A cast becomes its
    base plus the field offset; `sizeof` / `countof` a constant, or the
    `NAME.__size` symbol of an imported reservation."""
    return LinkExpr(tuple(_lowered_ops(_ordered(expression), resolver)))


def _lowered_ops(ordered: Sequence[ExprNode], resolver: Resolver) -> list[ExprOp]:
    ops: list[ExprOp] = []
    for node in ordered:
        if isinstance(node, UnaryOp):
            ops.append(unary_op(node.token.value))
        elif isinstance(node, BinOp):
            ops.append(binary_op(node.token.value))
        elif isinstance(node, SizeofExprNode):
            ops.extend(_lowered_size(node, resolver).ops)
        elif isinstance(node, CastAccessExprNode | CastValueExprNode):
            ops.extend(_lowered_ops(_ordered(ExpressionAstNode(list(node.inner))), resolver))
            if isinstance(node, CastAccessExprNode):
                ops.extend((*constant(_field_offset(node, resolver)).ops, binary_op("+")))
        else:
            ops.append(term_op(node.token))
    return ops


def _lowered_size(node: SizeofExprNode, resolver: Resolver) -> LinkExpr:
    from a816.parse.ast.size_of import size_of

    size = size_of(node, resolver)
    if isinstance(size, str):
        size = _known_size(size, resolver)
    return constant(size) if isinstance(size, int) else symbol(size)


def resolve_link_names(expression: LinkExpr, resolver: Resolver) -> LinkExpr:
    """`expression` with each name replaced by what the linker should read
    for it, in the current scope (see `link_name`)."""
    return expression.substitute({name: link_name(name, resolver) for name in expression.names()})


def link_name(name: str, resolver: Resolver) -> LinkExpr:
    """What the linker reads for `name` written in the current scope: an
    alias's expression, resolved where the alias was defined; a constant of
    a scope below the module root (an `.alloc` body's `_CELLS`, a `.for`
    variable) as its value, since no object exports it; a label under the
    name its object exports it (`Resolver.exported_label_name`); any other
    name as written."""
    alias = resolver.current_scope.lookup_alias(name)
    if alias is not None and alias != symbol(name):
        return alias
    value = _nested_constant(name, resolver)
    if value is not None:
        return constant(value)
    return symbol(resolver.exported_label_name(name))


def deferred_link_expression(
    error: ExternalExpressionReference | ExternalSymbolReference, resolver: Resolver
) -> LinkExpr:
    """The link-time expression an evaluation that met an extern defers to."""
    if isinstance(error, ExternalExpressionReference):
        return error.expression
    return resolve_link_names(symbol(error.symbol_name), resolver)


def _nested_constant(name: str, resolver: Resolver) -> int | None:
    """The value of `name` when a scope below the module root defines it as
    a number constant, and no label of that name is in reach."""
    if resolver.current_scope.find_label_scope(name) is not None:
        return None
    scope = resolver.current_scope
    while scope.parent is not None:
        value = scope.symbols.get(name)
        if value is not None:
            return value if isinstance(value, int) else None
        scope = scope.parent
    return None


def identifier_tokens(terms: Sequence[ExprNode]) -> list[Token]:
    """Every name an expression mentions, the ones inside a cast's
    `(inner as T)` included: a pooled label there relocates like any other."""
    found: list[Token] = []
    for term in terms:
        if isinstance(term, CastAccessExprNode | CastValueExprNode):
            found.extend(identifier_tokens(term.inner))
        elif isinstance(term, Term) and term.token.type == TokenType.IDENTIFIER:
            found.append(term.token)
    return found


def reconstruct_expression(expression: ExpressionAstNode, resolver: Resolver | None = None) -> str:
    """Reconstruct the original expression string from the AST.

    With a resolver, a cast lowers to plain arithmetic the linker can
    evaluate: `(inner as T).field` becomes `( inner + OFFSET )` and
    `(inner as T)` becomes `( inner )`.
    """
    return " ".join(_render_term(node, resolver) for node in expression.tokens)


def _known_size(name: str, resolver: Resolver) -> int | str:
    """A `NAME.__size` already measured in this module renders as its number;
    an imported one stays a symbol for the linker."""
    try:
        value = resolver.current_scope.value_for(name)
    except SymbolNotDefined, ExternalSymbolReference:
        return name
    return value if isinstance(value, int) else name


def _render_term(node: ExprNode, resolver: Resolver | None) -> str:
    if resolver is not None and isinstance(node, SizeofExprNode):
        from a816.parse.ast.size_of import size_of

        size = size_of(node, resolver)
        if isinstance(size, str):
            size = _known_size(size, resolver)
        return f"{size:#x}" if isinstance(size, int) else size
    if resolver is not None and isinstance(node, CastAccessExprNode | CastValueExprNode):
        inner = reconstruct_expression(ExpressionAstNode(list(node.inner)), resolver)
        if isinstance(node, CastValueExprNode):
            return f"( {inner} )"
        return f"( {inner} + {_field_offset(node, resolver):#x} )"
    return node.token.value if hasattr(node, "token") else str(node)


def expr_to_ast(expr_str: str, filename: str = "memory") -> ExpressionAstNode:
    """Parse `expr_str` alone; `filename` is the file its names are written in."""
    from a816.parse.parser import Parser
    from a816.parse.parser_states import parse_expression_ep
    from a816.parse.scanner import Scanner
    from a816.parse.scanner_states import lex_standalone_expression

    scanner = Scanner(lex_standalone_expression)
    tokens = scanner.scan(filename, expr_str)
    if scanner.errors:
        raise scanner.errors[0]
    parser = Parser(tokens, parse_expression_ep)
    nodes = parser.parse()
    first_node = nodes[0]
    assert isinstance(first_node, ExpressionAstNode)
    return first_node


def eval_expression_str(expr_str: str, resolver: Resolver, filename: str = "memory") -> int | str:
    expr_node = expr_to_ast(expr_str, filename)
    return eval_expression(expr_node, resolver)
