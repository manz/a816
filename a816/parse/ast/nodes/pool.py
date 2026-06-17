"""Pool / alloc / relocate / reclaim directive nodes."""

from __future__ import annotations

from typing import Any

from a816.parse.ast.nodes.base import AstNode, ExpressionAstNode
from a816.parse.ast.nodes.containers import BlockAstNode, _indent_block_body
from a816.parse.tokens import Token

PoolRangeExpr = tuple[ExpressionAstNode, ExpressionAstNode]


class PoolAstNode(AstNode):
    """AST node for `.pool NAME { ... }` directive.

    Declares a freespace pool with one or more byte ranges, a fill byte, and
    an allocation strategy. Range bounds and fill byte are stored as
    expressions and evaluated at codegen time so users can write
    `range BANK_BASE 0x028fff` instead of magic literals.
    """

    def __init__(
        self,
        name: str,
        ranges: list[PoolRangeExpr],
        fill: ExpressionAstNode,
        strategy: str,
        file_info: Token,
        bss: bool = False,
    ) -> None:
        super().__init__("pool", file_info)
        self.pool_name = name
        self.ranges = ranges
        self.fill = fill
        self.strategy = strategy
        self.bss = bss

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.pool_name, len(self.ranges), self.strategy

    def to_canonical(self) -> str:
        lines = [f".pool {self.pool_name} {{"]
        if self.bss:
            lines.append("    bss")
        for start, end in self.ranges:
            lines.append(f"    range {start.to_canonical()} {end.to_canonical()}")
        lines.append(f"    fill {self.fill.to_canonical()}")
        lines.append(f"    strategy {self.strategy}")
        lines.append("}")
        return "\n".join(lines)


class AllocAstNode(AstNode):
    """AST node for `.alloc` in three shapes:

    * Pooled: `.alloc NAME in POOL { body }` — allocator picks the
      address inside POOL's ranges.
    * Pinned named: `.alloc NAME at ADDR [size N] { body }` — body
      lands at ADDR; optional `size N` upper-bounds it (overflow =
      hard error).
    * Pinned anonymous: `.alloc at ADDR [size N] { body }` — same
      without a NAME (3-byte hijacks shouldn't tax with names).

    Pinned forms desugar to an anonymous single-range pool + an alloc
    into it at codegen, reusing the existing pool/alloc machinery for
    placement. `is_pinned` flips on when `at_address` is set.

    NOTE: planned `..END` range syntax (`at ADDR..END`) requires a
    scanner-level `..` token addition; for PR1 the equivalent is
    `at ADDR size N`. `..END` lands in a follow-up.
    """

    def __init__(
        self,
        name: str | None,
        pool_name: str | None,
        body: BlockAstNode,
        file_info: Token,
        *,
        at_address: ExpressionAstNode | None = None,
        at_size: ExpressionAstNode | None = None,
    ) -> None:
        super().__init__("alloc", file_info)
        self.name = name
        self.pool_name = pool_name
        self.body = body
        self.at_address = at_address
        self.at_size = at_size

    @property
    def is_pinned(self) -> bool:
        return self.at_address is not None

    def to_representation(self) -> tuple[Any, ...]:
        return (
            self.kind,
            self.name or "",
            self.pool_name or ("__pinned" if self.is_pinned else ""),
            self.body.to_representation()[0],
        )

    def to_canonical(self) -> str:
        body = _indent_block_body(self.body)
        name = self.name or ""
        head = f"{name} " if name else ""
        if self.is_pinned:
            addr = self.at_address.to_canonical() if self.at_address else "?"
            size = f" size {self.at_size.to_canonical()}" if self.at_size else ""
            # Pinned *inside* a named pool (`.reserve NAME SIZE at ADDR in POOL`)
            # keeps the `in POOL` tail; anonymous pins drop it.
            tail = f" in {self.pool_name}" if self.pool_name else ""
            return f".alloc {head}at {addr}{size}{tail} {{\n{body}\n}}"
        return f".alloc {head}in {self.pool_name} {{\n{body}\n}}"


class RelocateAstNode(AstNode):
    """AST node for `.relocate SYMBOL OLD_START OLD_END into POOL { body }`.

    Moves the labelled section `SYMBOL` from `[OLD_START, OLD_END]` into the
    named pool. The old range is reclaimed back into the pool (fill byte
    applied during emission) and `body` is placed at the allocator-chosen
    address; `SYMBOL` resolves to the new location.
    """

    def __init__(
        self,
        symbol: str,
        old_start: ExpressionAstNode,
        old_end: ExpressionAstNode,
        pool_name: str,
        body: BlockAstNode,
        file_info: Token,
    ) -> None:
        super().__init__("relocate", file_info)
        self.symbol = symbol
        self.old_start = old_start
        self.old_end = old_end
        self.pool_name = pool_name
        self.body = body

    def to_representation(self) -> tuple[Any, ...]:
        return (
            self.kind,
            self.symbol,
            self.pool_name,
            self.body.to_representation()[0],
        )

    def to_canonical(self) -> str:
        body = _indent_block_body(self.body)
        return (
            f".relocate {self.symbol} {self.old_start.to_canonical()} {self.old_end.to_canonical()} "
            f"into {self.pool_name} {{\n{body}\n}}"
        )


class ReclaimAstNode(AstNode):
    """AST node for `.reclaim POOL START END` directive.

    Adds the inclusive byte range `[START, END]` to the named pool and
    fills it with the pool's fill byte. Ranges crossing bank boundaries or
    overlapping existing pool ranges raise at resolution time.
    """

    def __init__(
        self,
        pool_name: str,
        start: ExpressionAstNode,
        end: ExpressionAstNode,
        file_info: Token,
    ) -> None:
        super().__init__("reclaim", file_info)
        self.pool_name = pool_name
        self.start = start
        self.end = end

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.pool_name

    def to_canonical(self) -> str:
        return f".reclaim {self.pool_name} {self.start.to_canonical()} {self.end.to_canonical()}"
