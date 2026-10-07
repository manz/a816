"""Block / Compound / Scope / If / For / Macro container nodes."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Final

from a816.parse.ast.nodes.base import AstNode, ExpressionAstNode
from a816.parse.tokens import Token


class BlockAstNode(AstNode):
    def __init__(self, body: Sequence[AstNode], file_info: Token) -> None:
        super().__init__("block", file_info)
        self.body: Final[tuple[AstNode, ...]] = tuple(body)

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, [node.to_representation() for node in self.body]

    def to_canonical(self) -> str:
        return f"{{\n{'\n'.join([node.to_canonical() for node in self.body])}}}\n"


class CompoundAstNode(AstNode):
    def __init__(self, body: Sequence[AstNode], file_info: Token):
        super().__init__("compound", file_info)
        self.body: Final[tuple[AstNode, ...]] = tuple(body)

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, [node.to_representation() for node in self.body]

    def to_canonical(self) -> str:
        return "\n".join([node.to_canonical() for node in self.body])


class ScopeAstNode(AstNode):
    def __init__(self, name: str, body: Any, file_info: Token, docstring: str | None = None) -> None:
        super().__init__("scope", file_info, docstring)
        self.name: Final[str] = name
        self.body: Final[BlockAstNode] = body

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.name, self.body.to_representation()

    def to_canonical(self) -> str:
        return f".scope {self.name}:\n{{{self.body.to_canonical()}}}\n"


class IfAstNode(AstNode):
    def __init__(
        self,
        expression: ExpressionAstNode,
        block: CompoundAstNode,
        else_bock: CompoundAstNode | None,
        file_info: Token,
    ):
        super().__init__("if", file_info)
        self.expression: Final[ExpressionAstNode] = expression
        self.block: Final[CompoundAstNode] = block
        self.else_block: Final[CompoundAstNode | None] = else_bock

    def to_representation(self) -> tuple[Any, ...]:
        return (
            self.kind,
            self.expression.to_representation()[0],
            self.block.to_representation(),
            self.else_block.to_representation() if self.else_block else None,
        )

    def to_canonical(self) -> str:
        condition = self.expression.to_canonical()
        block_content = self.block.to_canonical()
        if self.else_block:
            else_content = self.else_block.to_canonical()
            return f".if {condition}\n{block_content}.else\n{else_content}.endif"
        else:
            return f".if {condition}\n{block_content}.endif"


class ForAstNode(AstNode):
    def __init__(
        self,
        symbol: str,
        min_value: ExpressionAstNode,
        max_value: ExpressionAstNode,
        body: CompoundAstNode,
        file_info: Token,
    ) -> None:
        super().__init__("for", file_info)
        self.symbol: Final = symbol
        self.min_value: Final = min_value
        self.max_value: Final = max_value
        self.body: Final = body

    def to_representation(self) -> tuple[Any, ...]:
        return (
            self.kind,
            self.symbol,
            self.min_value.to_representation(),
            self.max_value.to_representation(),
            self.body.to_representation(),
        )

    def to_canonical(self) -> str:
        min_val = self.min_value.to_canonical()
        max_val = self.max_value.to_canonical()
        body_content = self.body.to_canonical()
        return f".for {self.symbol} {min_val} {max_val}\n{body_content}.endfor"


class MacroAstNode(AstNode):
    def __init__(
        self, name: str, args: Sequence[str], block: BlockAstNode, file_info: Token, docstring: str | None = None
    ):
        super().__init__("macro", file_info, docstring)
        self.name: Final[str] = name
        self.args: Final[tuple[str, ...]] = tuple(args)
        self.block: Final[BlockAstNode] = block

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.name, ("args", list(self.args)), self.block.to_representation()

    def to_canonical(self) -> str:
        args_str = ", ".join(self.args) if self.args else ""
        block_content = self.block.to_canonical()
        return f".macro {self.name}({args_str}) {{{block_content}}}"


def _indent_block_body(block: BlockAstNode, indent: str = "    ") -> str:
    """Render a block's children with consistent indentation, no surrounding braces.

    Used by `.alloc` and `.relocate` canonicalisation so fluff format can pass
    the source through a round-trip without losing the body's structure.
    """
    lines: list[str] = []
    for node in block.body:
        for line in node.to_canonical().splitlines() or [""]:
            lines.append(f"{indent}{line}" if line else line)
    return "\n".join(lines)
