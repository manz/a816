"""Symbol / extern / assign emitters + typed-bind expansion."""

from __future__ import annotations

from a816.exceptions import ExternalExpressionReference, ExternalSymbolReference
from a816.parse.ast.expression import eval_expression
from a816.parse.ast.nodes import (
    AssignAstNode,
    CastValueExprNode,
    ExpressionAstNode,
    ExternAstNode,
    SymbolAffectationAstNode,
)
from a816.parse.codegen.base import GenNodes, MacroDefinitions, generators
from a816.parse.nodes import ExternNode, NodeError, SymbolNode
from a816.parse.tokens import Token, TokenType
from a816.symbols import Resolver


def _expression_references_extern(value: ExpressionAstNode, resolver: Resolver) -> bool:
    return any(
        term.token.type == TokenType.IDENTIFIER and resolver.current_scope.is_external_symbol(term.token.value)
        for term in value.tokens
    )


def _try_eager_register_alias(node: SymbolAffectationAstNode, resolver: Resolver) -> None:
    try:
        eval_expression(node.value, resolver)
    except (ExternalExpressionReference, ExternalSymbolReference) as e:
        expr_str = e.symbol_name if isinstance(e, ExternalSymbolReference) else e.expression_str
        resolver.current_scope.add_external_alias(node.symbol, expr_str)
        object_writer = resolver.context.object_writer
        if object_writer is not None:
            object_writer.add_alias(node.symbol, expr_str)


def _try_eager_constant_bind(node: SymbolAffectationAstNode, resolver: Resolver) -> bool:
    """Bind `NAME = constant_expr` eagerly so downstream codegen can read it.

    Pool literals (`.pool`, `.reclaim`, `.relocate` addresses) eval at
    codegen time, which runs before `SymbolNode.pc_after` binds RHS values.
    For pure-constant RHS (no label / forward-symbol refs), we can resolve
    immediately and bind the LHS into the current scope so a following
    `.pool p { range NAME 0x028fff }` resolves cleanly.

    Returns True iff the binding was successful.
    """
    try:
        value = eval_expression(node.value, resolver)
    except Exception:  # SymbolNotDefined / external refs / non-int — fall through
        return False
    if not isinstance(value, int):
        return False
    resolver.current_scope.add_symbol(node.symbol, value)
    return True


def generate_symbol(
    node: SymbolAffectationAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    # If the RHS references a symbol already known to be external, register an
    # alias eagerly so subsequent code-gen sees the LHS as external too. Forward
    # refs to locally defined symbols still go through SymbolNode.pc_after.
    if (
        isinstance(node.value, ExpressionAstNode)
        and resolver.context.is_object_mode
        and _expression_references_extern(node.value, resolver)
    ):
        _try_eager_register_alias(node, resolver)
    # Eagerly bind constant RHS so codegen-time consumers (pool literals) see it.
    elif isinstance(node.value, ExpressionAstNode):
        _try_eager_constant_bind(node, resolver)

    return [SymbolNode(node.symbol, node.value, resolver)]


def generate_extern(
    node: ExternAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    # In object mode, register the extern eagerly so subsequent code-gen
    # (e.g. `font_ptr = extern_sym + N`) sees it as external. In direct mode
    # we leave it to ExternNode.pc_after to avoid shadowing real definitions
    # provided by included files.
    if resolver.context.is_object_mode:
        resolver.current_scope.add_external_symbol(node.symbol)
    return [ExternNode(node.symbol, resolver)]


def _try_typed_bind(node: AssignAstNode, resolver: Resolver, file_info: Token) -> bool:
    """If RHS is `(expr as T)`, eager-expand the instance's flat field symbols.

    Returns True iff the RHS was a typed cast and the expansion succeeded.
    Externs in the cast base aren't supported here — the user would lose the
    static field-access ergonomics anyway, so raise rather than silently
    falling back to a plain alias.
    """
    tokens = node.value.tokens
    if len(tokens) != 1 or not isinstance(tokens[0], CastValueExprNode):
        return False
    cast = tokens[0]
    type_name = cast.type_name
    if type_name not in resolver.struct_layouts:
        raise NodeError(
            f"Typed bind {node.symbol!r}: unknown struct type {type_name!r}.",
            file_info,
        )
    base = eval_expression(ExpressionAstNode(list(cast.inner)), resolver)
    if not isinstance(base, int):
        raise NodeError(
            f"Typed bind {node.symbol!r}: base expression must evaluate to an integer address.",
            file_info,
        )
    resolver.current_scope.add_symbol(node.symbol, base)
    for field_path, offset, _width in resolver.struct_layouts[type_name]:
        resolver.current_scope.add_symbol(f"{node.symbol}.{field_path}", base + offset)
    resolver.typed_instances[node.symbol] = type_name
    resolver.typed_instance_addr_width[node.symbol] = _address_width_for(base)
    return True


def _address_width_for(value: int) -> str:
    """Map an integer address to its natural 65c816 addressing-mode width.

    - `< 0x100`     → "b" (direct page; one-byte operand)
    - `< 0x10000`   → "w" (absolute; two-byte operand)
    - otherwise      → "l" (long; three-byte operand)
    """
    if value < 0x100:
        return "b"
    if value < 0x10000:
        return "w"
    return "l"


def generate_assign(
    node: AssignAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    if _try_typed_bind(node, resolver, file_info):
        return []

    try:
        value = eval_expression(node.value, resolver)
        resolver.current_scope.add_symbol(node.symbol, value)
    except (ExternalExpressionReference, ExternalSymbolReference) as e:
        if not resolver.context.is_object_mode:
            raise NodeError(
                f"{node.symbol} = {node.value.to_canonical()}: "
                f"external symbols only allowed in object compilation mode.",
                file_info,
            ) from e
        expr_str = e.symbol_name if isinstance(e, ExternalSymbolReference) else e.expression_str
        resolver.current_scope.add_external_alias(node.symbol, expr_str)
        object_writer = resolver.context.object_writer
        if object_writer is not None:
            object_writer.add_alias(node.symbol, expr_str)

    return []


generators["symbol"] = generate_symbol
generators["extern"] = generate_extern
generators["assign"] = generate_assign
