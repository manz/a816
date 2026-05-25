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
    CodePositionAstNode,
    ExpressionAstNode,
    PoolAstNode,
    ReclaimAstNode,
    RelocateAstNode,
)
from a816.parse.ast.visitor import walk
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
        if node.pool_name in resolver.pools:
            existing = resolver.pools[node.pool_name]
            same_shape = (
                [(r.start, r.end) for r in existing.ranges] == [(r.start, r.end) for r in pool.ranges]
                and existing.fill == pool.fill
                and existing.strategy == pool.strategy
            )
            if not same_shape:
                raise NodeError(f"pool {node.pool_name!r} already declared with different shape", file_info)
            # Identical re-declaration: importer picked up the same pool
            # via two paths (extern .o + inline source). Skip silently —
            # the existing decl is authoritative.
            return []
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

    if node.is_pinned:
        pool_name = _synthesize_pinned_pool(node, resolver, file_info)
        alloc_name = node.name or _anonymous_alloc_name(file_info, pool_name)
    else:
        if node.pool_name is None or node.pool_name not in resolver.pools:
            raise NodeError(f"alloc into unknown pool {node.pool_name!r}", file_info)
        pool_name = node.pool_name
        alloc_name = node.name or _anonymous_alloc_name(file_info, pool_name)

    _reject_nested_placement(node)
    body_nodes = _code_gen(node.body.body, resolver, macro_definitions)
    return [AllocNode(alloc_name, pool_name, body_nodes, resolver, file_info)]


_NESTED_PLACEMENT_KINDS = {
    AllocAstNode: ".alloc",
    RelocateAstNode: ".relocate",
    CodePositionAstNode: "`*=` (CodePosition)",
}


def _reject_nested_placement(node: AllocAstNode) -> None:
    """Forbid placement directives inside an `.alloc` body.

    Nested placement has no well-defined semantics: the inner directive
    would re-anchor the PC inside a region the outer `.alloc` already
    owns, silently corrupting layout. Fail loudly with both source
    locations so the author can hoist the inner block out.
    """
    outer = node.name or "<anonymous>"
    for child in walk(node.body.body):
        kind = _NESTED_PLACEMENT_KINDS.get(type(child))
        if kind is None:
            continue
        inner_pos = getattr(getattr(child.file_info, "position", None), "line", "?")
        raise NodeError(
            f"nested placement directive {kind} inside `.alloc {outer}` "
            f"(at line {inner_pos}) is not allowed; hoist it outside the alloc body",
            child.file_info,
        )


def _anonymous_alloc_name(file_info: Token, pool_name: str) -> str:
    """Auto-name for anonymous allocs. Stable per source location so
    repeat builds don't churn the linker's symbol map."""
    line = getattr(getattr(file_info, "position", None), "line", 0)
    column = getattr(getattr(file_info, "position", None), "column", 0)
    return f"__anon_alloc_{pool_name}_{line}_{column}"


def _synthesize_pinned_pool(
    node: AllocAstNode,
    resolver: Resolver,
    file_info: Token,
) -> str:
    """Pinned allocs desugar to an anonymous single-range pool plus an
    alloc into it. Pool is named for the source location so two pinned
    allocs at the same address (likely a bug) collide on pool decl
    rather than silently last-write-wins."""
    if node.at_address is None:  # pragma: no cover (defensive)
        raise NodeError("pinned alloc without at_address", file_info)
    addr = _eval_int(node.at_address, resolver, file_info)
    if node.at_size is not None:
        size = _eval_int(node.at_size, resolver, file_info)
        if size <= 0:
            raise NodeError(f"`.alloc at` size must be positive, got {size}", file_info)
        end = addr + size - 1
    else:
        # Unbounded: range extends to end of bank. The bank-overflow
        # check below builds extra per-bank ranges so a body that
        # spills past `$XX:FFFF` continues into `$XX+1:0000`. Bounded
        # forms (`at ADDR size N`) keep the single-range strict shape.
        end = (addr & 0xFF0000) | 0xFFFF
    line = getattr(getattr(file_info, "position", None), "line", 0)
    pool_name = f"__pinned_at_{addr:06X}_L{line}"
    if pool_name in resolver.pools:
        # Idempotent: paired-import inlines the same source into every
        # consumer, so the same `.alloc at ADDR { ... }` site gets
        # synthesized once per importer. Pool name encodes (addr, line)
        # — a true second declaration at the same address+line in the
        # same source can't happen, so reusing the existing pool is
        # safe. The AllocNode that owns this site requests its slot
        # against the existing pool; linker dedup
        # (_allocate_pools_across_modules) collapses duplicate alloc
        # requests by (pool, symbol) so the body bytes land once.
        return pool_name
    # Unbounded form opts out of the per-range bank-boundary guard so
    # `.incbin` payloads that span multiple banks place contiguously.
    # Bounded `at ADDR size N` keeps the strict bank-local check.
    if node.at_size is None:
        ranges = [PoolRange(start=addr, end=0xFFFFFF, allow_bank_cross=True)]
    else:
        ranges = [PoolRange(start=addr, end=end)]
    pool = Pool(
        name=pool_name,
        ranges=ranges,
        fill=0x00,
        strategy=Strategy.PACK,
    )
    resolver.pools[pool_name] = pool
    # Mirror `generate_pool`'s object-mode side effect: the linker needs
    # the synthetic pool's decl in the `.o` to satisfy the pool_alloc
    # request that the AllocNode will queue. Without this, link fails
    # with "alloc references undeclared pool".
    if resolver.context.is_object_mode and resolver.context.object_writer is not None:
        from a816.object_file import PoolDecl

        resolver.context.object_writer.pool_decls.append(
            PoolDecl(
                name=pool_name,
                ranges=[(r.start, r.end) for r in ranges],
                fill=0x00,
                strategy=Strategy.PACK.value,
            )
        )
    return pool_name


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
