"""Symbol / extern / assign emitters + typed-bind expansion."""

from __future__ import annotations

from a816.error_codes import (
    E_CODEGEN_TYPED_BIND_NON_INT,
    E_CODEGEN_TYPED_BIND_UNKNOWN_TYPE,
    E_SYMBOL_EAGER_FORWARD_REF,
    E_SYMBOL_EXTERNAL_NOT_ALLOWED,
)
from a816.exceptions import ExternalExpressionReference, ExternalSymbolReference, SymbolNotDefined
from a816.parse.ast.expression import canonicalize_local_label_refs, eval_expression, expr_to_ast
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
        canonical = canonicalize_local_label_refs(expr_str, resolver)
        resolver.register_external_alias(node.symbol, canonical)


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
    except Exception:  # noqa: BLE001 - SymbolNotDefined / external refs / non-int, fall through
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
    view = _lazy_typed_view(node, resolver, file_info)
    if view is not None:
        return view
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


def _lazy_typed_view(node: SymbolAffectationAstNode, resolver: Resolver, file_info: Token) -> GenNodes | None:
    """`view = (base as T)`: the lazy typed view, for a base known only later
    (a label placed at link, a forward reference).

    Expands to `view = base` and `view.field = ( base ) + offset` for every
    field, each an ordinary lazy symbol, so it resolves when the base does.
    `:=` is the eager form and needs the base now.
    """
    tokens = node.value.tokens if isinstance(node.value, ExpressionAstNode) else []
    if len(tokens) != 1 or not isinstance(tokens[0], CastValueExprNode):
        return None
    cast = tokens[0]
    if cast.type_name not in resolver.struct_layouts:
        raise NodeError(
            f"Typed view {node.symbol!r}: unknown struct type {cast.type_name!r}.",
            file_info,
            code=str(E_CODEGEN_TYPED_BIND_UNKNOWN_TYPE),
        )
    filename = _filename(file_info)
    base = ExpressionAstNode(list(cast.inner)).to_canonical()
    nodes: GenNodes = [SymbolNode(node.symbol, ExpressionAstNode(list(cast.inner)), resolver)]
    for field_path, offset, _width in resolver.struct_layouts[cast.type_name]:
        field = expr_to_ast(f"( {base} ) + {offset:#x}", filename)
        nodes.append(SymbolNode(f"{node.symbol}.{field_path}", field, resolver))
    resolver.typed_instances[node.symbol] = cast.type_name
    return nodes


def _filename(token: Token) -> str:
    position = token.position
    return position.file.filename if position is not None and position.file is not None else "memory"


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


def _eager_eval(node: AssignAstNode, expr: ExpressionAstNode, resolver: Resolver, file_info: Token) -> int | str:
    """Evaluate a `:=` RHS now; an undefined symbol is a forward reference."""
    try:
        return eval_expression(expr, resolver)
    except SymbolNotDefined as e:
        raise NodeError(
            f"`{e.name}` is not defined yet; `{node.symbol} :=` needs its value now",
            e.token or file_info,
            code=str(E_SYMBOL_EAGER_FORWARD_REF),
            hint="`:=` evaluates immediately; use `=` for a forward reference",
        ) from e


def _try_typed_bind(node: AssignAstNode, resolver: Resolver, file_info: Token) -> bool:
    """If RHS is `(expr as T)`, eager-expand the instance's flat field symbols.

    Returns True iff the RHS was a typed cast and the expansion succeeded.
    In object mode an extern base binds link-time aliases instead: the view
    and each `view.field` resolve to `base + offset` once the base is placed.
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
            code=str(E_CODEGEN_TYPED_BIND_UNKNOWN_TYPE),
        )
    inner = ExpressionAstNode(list(cast.inner))
    try:
        base = _eager_eval(node, inner, resolver, file_info)
    except (ExternalExpressionReference, ExternalSymbolReference) as e:
        if not resolver.context.is_object_mode:
            raise
        _bind_extern_view(node.symbol, type_name, _external_expression(e, resolver), resolver)
        return True
    if not isinstance(base, int):
        raise NodeError(
            f"Typed bind {node.symbol!r}: base expression must evaluate to an integer address.",
            file_info,
            code=str(E_CODEGEN_TYPED_BIND_NON_INT),
        )
    resolver.current_scope.add_symbol(node.symbol, base)
    for field_path, offset, _width in resolver.struct_layouts[type_name]:
        resolver.current_scope.add_symbol(f"{node.symbol}.{field_path}", base + offset)
    resolver.typed_instances[node.symbol] = type_name
    resolver.typed_instance_addr_width[node.symbol] = _address_width_for(base)
    return True


def _external_expression(e: ExternalExpressionReference | ExternalSymbolReference, resolver: Resolver) -> str:
    expr_str = e.symbol_name if isinstance(e, ExternalSymbolReference) else e.expression_str
    return canonicalize_local_label_refs(expr_str, resolver)


def _bind_extern_view(symbol: str, type_name: str, base: str, resolver: Resolver) -> None:
    """`view := (extern as T)`: alias the view and every field to link-time
    expressions over the extern base."""
    resolver.register_external_alias(symbol, base)
    for field_path, offset, _width in resolver.struct_layouts[type_name]:
        resolver.register_external_alias(f"{symbol}.{field_path}", f"( {base} ) + {offset:#x}")


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
        value = _eager_eval(node, node.value, resolver, file_info)
        resolver.current_scope.add_symbol(node.symbol, value)
    except (ExternalExpressionReference, ExternalSymbolReference) as e:
        if not resolver.context.is_object_mode:
            raise NodeError(
                f"{node.symbol} = {node.value.to_canonical()}: "
                f"external symbols only allowed in object compilation mode.",
                file_info,
                code=str(E_SYMBOL_EXTERNAL_NOT_ALLOWED),
            ) from e
        resolver.register_external_alias(node.symbol, _external_expression(e, resolver))

    return []


generators["symbol"] = generate_symbol
generators["extern"] = generate_extern
generators["assign"] = generate_assign
