"""`.pool` / `.alloc` / `.relocate` / `.reclaim` emitters + literal eval."""

from __future__ import annotations

from a816.exceptions import (
    ExternalExpressionReference,
    ExternalSymbolReference,
    SymbolNotDefined,
)
from a816.parse.ast.expression import eval_expression
from a816.parse.ast.nodes import (
    AllocAstNode,
    ExpressionAstNode,
    PoolAstNode,
    ReclaimAstNode,
    RelocateAstNode,
)
from a816.parse.codegen.base import GenNodes, MacroDefinitions, _code_gen, generators
from a816.parse.nodes import NodeError
from a816.parse.tokens import Token
from a816.pool import Pool, PoolRange, Strategy
from a816.symbols import Resolver


def _eval_int(expr: ExpressionAstNode, resolver: Resolver, where: Token) -> int:
    """Evaluate an expression to a concrete int at code-generation time.

    Pool literal positions (range bounds, fill byte, reclaim/relocate
    addresses) must resolve to constants — they feed the allocator
    immediately and cannot defer like a label reference.
    """
    try:
        value = eval_expression(expr, resolver)
    except (ExternalExpressionReference, ExternalSymbolReference) as exc:
        ref = exc.symbol_name if isinstance(exc, ExternalSymbolReference) else exc.expression_str
        raise NodeError(
            f"pool literal must be a constant expression (got external reference {ref!r})",
            where,
        ) from exc
    except SymbolNotDefined as exc:
        raise NodeError(
            f"pool literal references undefined symbol {exc!s}; pool decls evaluate "
            "at code-generation time before forward refs are bound",
            where,
        ) from exc
    if not isinstance(value, int):
        raise NodeError(
            f"pool literal must evaluate to int, got {type(value).__name__}",
            where,
        )
    return value


def generate_pool(
    node: PoolAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    if node.pool_name in resolver.pools:
        raise NodeError(f"pool {node.pool_name!r} already declared", file_info)
    try:
        ranges = [
            PoolRange(
                start=_eval_int(lo, resolver, file_info),
                end=_eval_int(hi, resolver, file_info),
            )
            for lo, hi in node.ranges
        ]
        fill_value = _eval_int(node.fill, resolver, file_info)
        if not 0 <= fill_value <= 0xFF:
            raise NodeError(
                f"pool {node.pool_name!r} fill 0x{fill_value:x} out of byte range",
                file_info,
            )
        pool = Pool(
            name=node.pool_name,
            ranges=ranges,
            fill=fill_value,
            strategy=Strategy(node.strategy),
        )
    except NodeError:
        raise
    except Exception as exc:  # PoolError, PoolInvalidRangeError, PoolOverlapError
        raise NodeError(f"pool {node.pool_name!r}: {exc}", file_info) from exc
    resolver.pools[node.pool_name] = pool
    _publish_pool_stats(node.pool_name, pool, resolver)
    if resolver.context.is_object_mode and resolver.context.object_writer is not None:
        from a816.object_file import PoolDecl

        resolver.context.object_writer.pool_decls.append(
            PoolDecl(
                name=pool.name,
                ranges=[(r.start, r.end) for r in pool.ranges],
                fill=pool.fill,
                strategy=pool.strategy.value,
            )
        )
    return []


def _publish_pool_stats(name: str, pool: Pool, resolver: Resolver) -> None:
    """Bind `<name>.capacity / fragments / largest_chunk` as scope symbols.

    Snapshot at declaration time — pre-allocator. Sufficient for the
    common case (`.if pool.capacity < N { ... }` guard). Post-allocator
    stats are recomputed when AllocNodes run; the snapshot stays accurate
    only for capacity-style values that don't change after declaration.
    """
    scope = resolver.current_scope
    for stat, value in (
        (f"{name}.capacity", pool.capacity),
        (f"{name}.fragments", pool.fragments),
        (f"{name}.largest_chunk", pool.largest_chunk),
    ):
        scope.add_symbol(stat, value)
        resolver.pool_stat_symbol_names.add(stat)


def generate_reclaim(
    node: ReclaimAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    pool = resolver.pools.get(node.pool_name)
    if pool is None:
        raise NodeError(f"reclaim into unknown pool {node.pool_name!r}", file_info)
    start = _eval_int(node.start, resolver, file_info)
    end = _eval_int(node.end, resolver, file_info)
    try:
        pool.reclaim(PoolRange(start=start, end=end))
    except Exception as exc:
        raise NodeError(f"reclaim into pool {node.pool_name!r}: {exc}", file_info) from exc
    return []


def generate_alloc(
    node: AllocAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    from a816.parse.nodes import AllocNode

    if node.pool_name not in resolver.pools:
        raise NodeError(f"alloc into unknown pool {node.pool_name!r}", file_info)
    body_nodes = _code_gen(node.body.body, resolver, macro_definitions)
    return [AllocNode(node.name, node.pool_name, body_nodes, resolver, file_info)]


def generate_relocate(
    node: RelocateAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    from a816.parse.nodes import RelocateNode

    if node.pool_name not in resolver.pools:
        raise NodeError(f"relocate into unknown pool {node.pool_name!r}", file_info)
    old_start = _eval_int(node.old_start, resolver, file_info)
    old_end = _eval_int(node.old_end, resolver, file_info)
    body_nodes = _code_gen(node.body.body, resolver, macro_definitions)
    return [
        RelocateNode(
            node.symbol,
            old_start,
            old_end,
            node.pool_name,
            body_nodes,
            resolver,
            file_info,
        )
    ]


generators["pool"] = generate_pool
generators["alloc"] = generate_alloc
generators["relocate"] = generate_relocate
generators["reclaim"] = generate_reclaim
