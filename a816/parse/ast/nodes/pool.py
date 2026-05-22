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
    ) -> None:
        super().__init__("pool", file_info)
        self.pool_name = name
        self.ranges = ranges
        self.fill = fill
        self.strategy = strategy

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.pool_name, len(self.ranges), self.strategy

    def to_canonical(self) -> str:
        lines = [f".pool {self.pool_name} {{"]
        for start, end in self.ranges:
            lines.append(f"    range {start.to_canonical()} {end.to_canonical()}")
        lines.append(f"    fill {self.fill.to_canonical()}")
        lines.append(f"    strategy {self.strategy}")
        lines.append("}")
        return "\n".join(lines)


class AllocAstNode(AstNode):
    """AST node for `.alloc NAME in POOL { body }` directive.

    Reserves space for `body` in the named pool. Final address is assigned
    by the pool allocator after first-pass sizing.
    """

    def __init__(
        self,
        name: str,
        pool_name: str,
        body: BlockAstNode,
        file_info: Token,
    ) -> None:
        super().__init__("alloc", file_info)
        self.name = name
        self.pool_name = pool_name
        self.body = body

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.name, self.pool_name, self.body.to_representation()[0]

    def to_canonical(self) -> str:
        body = _indent_block_body(self.body)
        return f".alloc {self.name} in {self.pool_name} {{\n{body}\n}}"


class RelocateAstNode(AstNode):
    """AST node for `.relocate SYMBOL OLD_START OLD_END into POOL { body }`.

    Moves the labelled region `SYMBOL` from `[OLD_START, OLD_END]` into the
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
