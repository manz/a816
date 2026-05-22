"""`.struct` AST node."""

from __future__ import annotations

from typing import Any

from a816.parse.ast.nodes.base import AstNode
from a816.parse.tokens import Token


class StructAstNode(AstNode):
    def __init__(self, name: str, fields: list[tuple[str, str]], file_info: Token) -> None:
        super().__init__("struct", file_info)
        self.name = name
        # Insertion-ordered `(name, type)` entries. Bit-field widths live in
        # the type string itself (`uN`); the codegen extracts the digits.
        # List instead of dict so downstream consumers can rely on the
        # declared order without poking at dict semantics, and so duplicate
        # names get caught at parse time.
        self.fields: list[tuple[str, str]] = list(fields)

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.name, list(self.fields)

    def to_canonical(self) -> str:
        body = "\n".join(f"    {field_type} {field_name}" for field_name, field_type in self.fields)
        return f".struct {self.name} {{\n{body}\n}}"
