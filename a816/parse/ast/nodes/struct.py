"""`.struct` AST node."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from a816.parse.ast.nodes.base import AstNode
from a816.parse.tokens import Token

StructBodyItem = tuple[str, str, str | None]
"""One line of a struct body, in source order: `("field", "type name", trailing
comment or None)`, `("comment", text, None)`, `("continuation", text, None)`
(a comment line aligned under the trailing comment above it) or
`("blank", "", None)`."""


def _comment_text(raw: str) -> str:
    comment = raw.strip()
    if comment.startswith((";", "/*")):
        return comment
    return f"; {comment}"


class StructAstNode(AstNode):
    def __init__(
        self,
        name: str,
        fields: Sequence[tuple[str, str]],
        file_info: Token,
        body: Sequence[StructBodyItem] = (),
    ) -> None:
        super().__init__("struct", file_info)
        self.name = name
        # Formatting view of the body: fields with their comments and the
        # blank lines between groups. Without one (built programmatically),
        # the fields alone are the body.
        self.body: tuple[StructBodyItem, ...] = tuple(body)
        # Insertion-ordered `(name, type)` entries. Bit-field widths live in
        # the type string itself (`uN`), as do array counts (`byte[21]`);
        # the codegen extracts the digits.
        # A sequence instead of a dict so downstream consumers can rely on the
        # declared order without poking at dict semantics, and so duplicate
        # names get caught at parse time.
        self.fields: tuple[tuple[str, str], ...] = tuple(fields)

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.name, list(self.fields)

    def to_canonical(self) -> str:
        items = self.body or [("field", f"{field_type} {field_name}", None) for field_name, field_type in self.fields]
        lines = [f".struct {self.name} {{"]
        trailing_column = 0
        for kind, text, trailing in items:
            if kind == "blank":
                lines.append("")
            elif kind == "comment":
                lines.append(f"    {_comment_text(text)}")
            elif kind == "continuation":
                lines.append(" " * trailing_column + _comment_text(text))
            elif trailing:
                field = f"    {text}  "
                trailing_column = len(field)
                lines.append(field + _comment_text(trailing))
            else:
                lines.append(f"    {text}")
        lines.append("}")
        return "\n".join(lines)
