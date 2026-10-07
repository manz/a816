"""`.pool` / `.alloc` / `.relocate` / `.reclaim` emitters + literal eval."""

from __future__ import annotations

from a816.error_codes import (
    E_CODEGEN_BAD_ALIGN,
    E_CODEGEN_BAD_POOL,
    E_CODEGEN_BAD_RECLAIM,
    E_CODEGEN_BAD_SIZE,
    E_CODEGEN_CROSS_BANK_BODY,
    E_CODEGEN_NESTED_PLACEMENT,
    E_CODEGEN_NODE_ERROR,
    E_CODEGEN_POOL_REDECLARED,
    E_CODEGEN_UNMAPPED_BANK,
    E_SYMBOL_NOT_DEFINED,
    E_SYMBOL_RESERVE_UNKNOWN_TYPE,
    E_SYMBOL_UNKNOWN_POOL,
)
from a816.exceptions import (
    A816Error,
    ExternalExpressionReference,
    ExternalSymbolReference,
    SymbolNotDefined,
)
from a816.parse.ast.expression import eval_expression, expr_to_ast, reconstruct_expression
from a816.parse.ast.nodes import (
    AllocAstNode,
    AssertAstNode,
    AstNode,
    BlockAstNode,
    CodePositionAstNode,
    ExpressionAstNode,
    LabelAstNode,
    PoolAstNode,
    ReclaimAstNode,
    RelocateAstNode,
    ReserveAstNode,
    ReserveTypedAstNode,
)
from a816.parse.ast.visitor import walk
from a816.parse.codegen.base import GenNodes, MacroDefinitions, _code_gen_placement_body, generators
from a816.parse.nodes import NodeError
from a816.parse.tokens import Token
from a816.pool import Pool, PoolRange, Strategy
from a816.protocols import NodeProtocol
from a816.section import ANONYMOUS_ALLOC_PREFIX, PINNED_POOL_PREFIX
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
            code=str(E_CODEGEN_BAD_POOL),
        ) from exc
    except SymbolNotDefined as exc:
        raise NodeError(
            f"pool literal references undefined symbol {exc!s}; pool decls evaluate "
            "at code-generation time before forward refs are bound",
            exc.token or where,
            code=str(E_SYMBOL_NOT_DEFINED),
        ) from exc
    if not isinstance(value, int):
        raise NodeError(
            f"pool literal must evaluate to int, got {type(value).__name__}",
            where,
            code=str(E_CODEGEN_BAD_POOL),
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
            piece
            for lo, hi in node.ranges
            for piece in _bank_local_ranges(
                _eval_int(lo, resolver, file_info), _eval_int(hi, resolver, file_info), node, resolver, file_info
            )
        ]
        fill_value = _eval_int(node.fill, resolver, file_info)
        if not 0 <= fill_value <= 0xFF:
            raise NodeError(
                f"pool {node.pool_name!r} fill 0x{fill_value:x} out of byte range",
                file_info,
                code=str(E_CODEGEN_BAD_POOL),
            )
        pool = Pool(
            name=node.pool_name,
            ranges=ranges,
            fill=fill_value,
            strategy=Strategy(node.strategy),
            bss=node.bss,
        )
        if node.pool_name in resolver.pools:
            _merge_pool_declaration(resolver.pools[node.pool_name], pool, node, resolver, file_info)
            return []
        resolver.pool_sources[node.pool_name] = _source_file(file_info)
    except NodeError:
        raise
    except Exception as exc:  # PoolError, PoolInvalidRangeError, PoolOverlapError
        raise NodeError(f"pool {node.pool_name!r}: {exc}", file_info, code=str(E_CODEGEN_BAD_POOL)) from exc
    _register_pool(pool, resolver)
    # Each context is its own allocator over the pool's memory: `POOL.CTX`.
    # Contexts of one pool never live at the same time, so the linker lets
    # their reservations share bytes (and nothing else).
    for context in node.contexts:
        _register_pool(
            Pool(
                name=f"{node.pool_name}.{context}",
                ranges=[PoolRange(start=r.start, end=r.end) for r in ranges],
                fill=fill_value,
                strategy=Strategy(node.strategy),
                bss=True,
                context=context,
            ),
            resolver,
        )
    return []


def _bank_local_ranges(lo: int, hi: int, node: PoolAstNode, resolver: Resolver, file_info: Token) -> list[PoolRange]:
    """`range LO HI` as bank-local ranges: one per bank, clipped to the
    windows the bus serves for this pool's kind (ROM, or writable for bss).

    Blocks stay bank-local, so a range over several banks is just shorthand
    for one range per bank. Clipping keeps a LoROM `range 0x228000 0x2fffff`
    to the `$8000-$FFFF` halves instead of handing out the low halves too.
    """
    if lo >> 16 == hi >> 16:
        return [PoolRange(start=lo, end=hi)]
    pieces: list[PoolRange] = []
    for bank in range((lo >> 16), (hi >> 16) + 1):
        windows = resolver.bus.windows_in(bank, writable=node.bss)
        if not windows:
            raise NodeError(
                f"pool {node.pool_name!r} range 0x{lo:06x}..0x{hi:06x} covers bank ${bank:02X}, "
                f"which no `.map` serves as {'memory' if node.bss else 'ROM'}",
                file_info,
                hint="declare the `.map` for these banks before the pool, or end the range before them",
                code=str(E_CODEGEN_UNMAPPED_BANK),
            )
        bank_lo, bank_hi = max(lo, bank << 16), min(hi, bank << 16 | 0xFFFF)
        for w_lo, w_hi in windows:
            start, end = max(bank_lo, bank << 16 | w_lo), min(bank_hi, bank << 16 | w_hi)
            if start <= end:
                pieces.append(PoolRange(start=start, end=end))
    return pieces


def _merge_pool_declaration(
    existing: Pool, pool: Pool, node: PoolAstNode, resolver: Resolver, file_info: Token
) -> None:
    """A pool declared again, by an import or by this module: union the
    ranges, as the linker does across objects (`Linker._merge_pool_decls`).

    Two modules may each contribute ranges to one pool; an identical
    re-declaration adds nothing. Fill, strategy, `bss` and `contexts` must
    agree, and a range may not overlap one the pool already has.
    """
    disagreement = _pool_disagreement(existing, pool, node, resolver)
    if disagreement is not None:
        raise NodeError(
            f"pool {node.pool_name!r} already declared with a different {disagreement}",
            file_info,
            code=str(E_CODEGEN_POOL_REDECLARED),
        )
    known = {(r.start, r.end) for r in existing.ranges}
    added = [r for r in pool.ranges if (r.start, r.end) not in known]
    if not added:
        return
    if resolver.pool_sources.get(node.pool_name) == _source_file(file_info):
        raise NodeError(
            f"pool {node.pool_name!r} already declared in this file",
            file_info,
            hint=f"declare it once; `.reclaim {node.pool_name} START END` adds a range",
            code=str(E_CODEGEN_POOL_REDECLARED),
        )
    for context_pool in [existing, *(resolver.pools[f"{node.pool_name}.{ctx}"] for ctx in node.contexts)]:
        for r in added:
            context_pool.reclaim(PoolRange(start=r.start, end=r.end))
    _publish_pool_stats(existing.name, existing, resolver)


def _source_file(file_info: Token) -> str:
    position = file_info.position
    return position.file.filename if position is not None and position.file is not None else ""


def _pool_disagreement(existing: Pool, pool: Pool, node: PoolAstNode, resolver: Resolver) -> str | None:
    """The first pool property two declarations disagree on, or None."""
    if existing.fill != pool.fill:
        return "fill"
    if existing.strategy != pool.strategy:
        return "strategy"
    if existing.bss != pool.bss:
        return "bss flag"
    if _declared_contexts(node.pool_name, resolver) != sorted(node.contexts):
        return "contexts list"
    return None


def _declared_contexts(pool_name: str, resolver: Resolver) -> list[str]:
    """Contexts already registered for `pool_name` (its `POOL.CTX` siblings)."""
    return sorted(
        p.context for p in resolver.pools.values() if p.context is not None and p.name == f"{pool_name}.{p.context}"
    )


def _register_pool(pool: Pool, resolver: Resolver) -> None:
    resolver.pools[pool.name] = pool
    _publish_pool_stats(pool.name, pool, resolver)
    if resolver.context.is_object_mode and resolver.context.object_writer is not None:
        from a816.object_file import PoolDecl

        resolver.context.object_writer.pool_decls.append(
            PoolDecl(
                name=pool.name,
                ranges=[(r.start, r.end) for r in pool.ranges],
                fill=pool.fill,
                strategy=pool.strategy.value,
                bss=pool.bss,
                context=pool.context,
            )
        )


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
        raise _unknown_pool_error("reclaim", node.pool_name, file_info)
    start = _eval_int(node.start, resolver, file_info)
    end = _eval_int(node.end, resolver, file_info)
    try:
        pool.reclaim(PoolRange(start=start, end=end))
    except Exception as exc:
        raise NodeError(
            f"reclaim into pool {node.pool_name!r}: {exc}", file_info, code=str(E_CODEGEN_BAD_RECLAIM)
        ) from exc
    return []


def generate_alloc(
    node: AllocAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    from a816.parse.nodes import AllocNode, PopScopeNode, ScopeNode

    pinned_addr: int | None = None
    if node.is_pinned and node.pool_name is None:
        # `.alloc at ADDR { body }`: synthesize a per-location pool that the
        # body floats into (the pool's single range starts at ADDR).
        pool_name = _synthesize_pinned_pool(node, resolver, file_info)
        alloc_name = node.name or _anonymous_alloc_name(file_info, pool_name)
    else:
        if node.pool_name is None or node.pool_name not in resolver.pools:
            raise _unknown_pool_error("alloc", node.pool_name, node.pool_token or file_info)
        pool_name = node.pool_name
        alloc_name = node.name or _anonymous_alloc_name(file_info, pool_name)
        if node.is_pinned and node.at_address is not None:
            # `.reserve NAME SIZE at ADDR in POOL`: pin the slot at ADDR
            # inside the existing (named) pool; the allocator validates the
            # span is in-range and overlap-free rather than choosing it.
            pinned_addr = _eval_int(node.at_address, resolver, file_info)

    _reject_nested_placement(node)
    _record_alloc_name(node, resolver)
    if node.cross_bank:
        _check_cross_bank_body(node)
    align = _eval_align(node, resolver, file_info)
    # Open an AllocBodyScope around the body so per-block underscore
    # labels (`_skip`, `_end`) stay private to this alloc; otherwise
    # two sibling allocs declaring `_skip:` silently overwrite each
    # other in the module's flat label namespace and `bne _skip` lands
    # in the wrong block. Non-underscore body labels bubble back to the
    # parent on PopScope so cross-alloc public refs still resolve.
    resolver.append_alloc_body_scope()
    body_scope = resolver.scopes[-1]
    resolver.use_next_scope()
    body_nodes: list[NodeProtocol] = [ScopeNode(resolver)]
    body_nodes += _code_gen_placement_body(node.body.body, resolver, macro_definitions)
    body_nodes.append(PopScopeNode(resolver, exports=True))
    resolver.restore_scope(exports=True)
    return [
        AllocNode(
            alloc_name,
            pool_name,
            body_nodes,
            resolver,
            file_info,
            pinned_addr=pinned_addr,
            pool_token=node.pool_token,
            body_scope=body_scope,
            align=align,
            cross_bank=node.cross_bank,
        )
    ]


def _record_alloc_name(node: AllocAstNode, resolver: Resolver) -> None:
    """Make a named alloc (and a reservation's size) known to `sizeof` before the body is measured."""
    if not node.name:
        return
    resolver.alloc_names.add(node.name)
    if node.reserve:
        resolver.reservation_sizes[node.name] = _reserved_size(node, resolver)


def _reserved_size(node: AllocAstNode, resolver: Resolver) -> int | None:
    """A flat `.reserve NAME SIZE` size, when SIZE is already a constant here;
    None leaves `sizeof` to the alloc's measured `NAME.__size`."""
    reserve = node.body.body[0] if node.body.body else None
    if not isinstance(reserve, ReserveAstNode):
        return None
    try:
        size = eval_expression(reserve.size, resolver)
    except A816Error:
        return None
    return size if isinstance(size, int) else None


def _check_cross_bank_body(node: AllocAstNode) -> None:
    """A `cross_bank` blob is read from its base by code that steps bank
    edges itself: only data may sit in it. Code can't execute through an
    edge, and a label inside would need an address a816 doesn't track
    across the edge."""
    from a816.parse.ast.nodes import CommentAstNode, DataNode, DocstringAstNode, IncludeBinaryAstNode

    allowed = (CommentAstNode, DataNode, DocstringAstNode, IncludeBinaryAstNode)
    for child in node.body.body:
        if not isinstance(child, allowed):
            raise NodeError(
                f"`cross_bank` alloc {node.name or ''!s} may hold only data (`.incbin`, `.db`/`.dw`/`.dl`)",
                child.file_info,
                code=str(E_CODEGEN_CROSS_BANK_BODY),
                hint="readers work from the alloc's base; move code and labels outside the blob",
            )


def _eval_align(node: AllocAstNode, resolver: Resolver, file_info: Token) -> int:
    """`align N` as an int: a power of two, 1 when absent."""
    if node.align is None:
        return 1
    align = _eval_int(node.align, resolver, file_info)
    if align <= 0 or align & (align - 1):
        raise NodeError(
            f"alloc {node.name or ''!s} `align {align}` is not a power of two", file_info, code=str(E_CODEGEN_BAD_ALIGN)
        )
    if node.at_address is not None and _eval_int(node.at_address, resolver, file_info) % align:
        raise NodeError(
            f"alloc {node.name or ''!s} is pinned off its `align {align}` boundary",
            file_info,
            code=str(E_CODEGEN_BAD_ALIGN),
        )
    return align


def generate_reserve_typed(
    node: ReserveTypedAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    """Expand `.reserve NAME as TYPE in POOL` against TYPE's layout.

    Builds an `.alloc NAME in POOL { ... }` whose body lays out a label
    `NAME.<field>` at each struct offset (padding the gaps with `.res`) and
    reserves the struct's full size. The field labels bind as alloc-body
    labels, so the linker rebases them with NAME to the allocator-chosen
    address: the byte-less equivalent of a typed bind over fixed memory.
    """
    if node.type_name not in resolver.struct_sizes:
        raise NodeError(
            f".reserve {node.name!r} as unknown struct type {node.type_name!r}",
            node.type_token or file_info,
            code=str(E_SYMBOL_RESERVE_UNKNOWN_TYPE),
        )
    size = resolver.struct_sizes[node.type_name]
    layout = sorted(resolver.struct_layouts[node.type_name], key=lambda field: field[1])

    body: list[AstNode] = []
    cursor = 0
    for field_path, offset, _width in layout:
        if offset > cursor:
            body.append(ReserveAstNode(expr_to_ast(hex(offset - cursor)), file_info))
            cursor = offset
        body.append(LabelAstNode(f"{node.name}.{field_path}", file_info))
    if size > cursor:
        body.append(ReserveAstNode(expr_to_ast(hex(size - cursor)), file_info))

    resolver.typed_instances[node.name] = node.type_name
    resolver.reservation_sizes[node.name] = size
    alloc = AllocAstNode(
        node.name,
        node.pool_name,
        BlockAstNode(body, file_info),
        file_info,
        pool_token=node.pool_token,
        at_address=node.at_address,
    )
    return generate_alloc(alloc, resolver, macro_definitions, file_info)


def _unknown_pool_error(directive: str, pool_name: str | None, where: Token) -> NodeError:
    """Located error for a placement directive naming an undeclared pool."""
    return NodeError(
        f"{directive} into unknown pool {pool_name!r}",
        where,
        code=str(E_SYMBOL_UNKNOWN_POOL),
        hint="declare it with `.pool NAME { range LO HI }` before placing into it",
    )


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
            code=str(E_CODEGEN_NESTED_PLACEMENT),
        )


def _anonymous_alloc_name(file_info: Token, pool_name: str) -> str:
    """Auto-name for anonymous allocs. Stable per source location so
    repeat builds don't churn the linker's symbol map."""
    line = getattr(getattr(file_info, "position", None), "line", 0)
    column = getattr(getattr(file_info, "position", None), "column", 0)
    return f"{ANONYMOUS_ALLOC_PREFIX}{pool_name}_{line}_{column}"


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
        raise NodeError("pinned alloc without at_address", file_info, code=str(E_CODEGEN_NODE_ERROR))
    addr = _eval_int(node.at_address, resolver, file_info)
    if node.at_size is not None:
        size = _eval_int(node.at_size, resolver, file_info)
        if size <= 0:
            raise NodeError(f"`.alloc at` size must be positive, got {size}", file_info, code=str(E_CODEGEN_BAD_SIZE))
        end = addr + size - 1
    else:
        # Unbounded: range extends to end of bank. The bank-overflow
        # check below builds extra per-bank ranges so a body that
        # spills past `$XX:FFFF` continues into `$XX+1:0000`. Bounded
        # forms (`at ADDR size N`) keep the single-range strict shape.
        end = (addr & 0xFF0000) | 0xFFFF
    line = getattr(getattr(file_info, "position", None), "line", 0)
    pool_name = f"{PINNED_POOL_PREFIX}{addr:06X}_L{line}"
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
                bss=False,  # pinned allocs emit bytes; never byte-less
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
        raise _unknown_pool_error("relocate", node.pool_name, node.pool_token or file_info)
    old_start = _eval_int(node.old_start, resolver, file_info)
    old_end = _eval_int(node.old_end, resolver, file_info)
    body_nodes = _code_gen_placement_body(node.body.body, resolver, macro_definitions)
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
generators["reserve_typed"] = generate_reserve_typed
generators["relocate"] = generate_relocate
generators["reclaim"] = generate_reclaim


def generate_assert(
    node: AssertAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    """Queue a `.assert` until every address is final: for the linker in
    object mode, for the end of label resolution in a direct build. It may
    name pooled labels, which only have their address after placement.
    Parse-only runs (LSP) skip it."""
    from a816.object_file import LinkAssert

    # Rendered with the resolver: `sizeof` / `countof` fold to numbers and
    # casts to arithmetic here, since the linker has no struct layouts.
    expression = reconstruct_expression(node.expression, resolver)
    check = LinkAssert(expression, node.message, _source_of_token(file_info))
    if resolver.context.is_object_mode and resolver.context.object_writer is not None:
        resolver.context.object_writer.asserts.append(check)
    elif resolver.context.is_direct_mode:
        resolver.direct_asserts.append(check)
    return []


def _source_of_token(token: Token) -> str:
    position = token.position
    if position is None or position.file is None:
        return ""
    return f"{position.file.filename}:{position.line + 1}"


generators["assert"] = generate_assert
