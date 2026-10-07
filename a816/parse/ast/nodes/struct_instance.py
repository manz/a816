"""`.istruct T { field = value, ... }` AST: a struct instance emitted as data.

Initializer values are an expression, a quoted string (byte arrays), a
`[ ... ]` list (arrays) or a nested `{ ... }` (struct fields). Comments
inside the braces are kept as items so the formatter can round-trip them.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from a816.parse.ast.nodes.base import AstNode, ExpressionAstNode
from a816.parse.ast.nodes.directives import CommentAstNode
from a816.parse.tokens import Token

_INDENT = "    "


class InitCommentAstNode(CommentAstNode):
    """A `; comment` inside an initializer; ``trailing`` when it ends a value's line."""

    def __init__(self, comment: str, trailing: bool, file_info: Token) -> None:
        super().__init__(comment, file_info)
        self.trailing = trailing


class StringInitAstNode(AstNode):
    """Quoted string initializer for a byte array; ``text`` is unquoted."""

    def __init__(self, file_info: Token) -> None:
        super().__init__("string_init", file_info)
        self.text = file_info.value[1:-1]

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.text

    def to_canonical(self) -> str:
        return self.file_info.value


class ListInitAstNode(AstNode):
    """`[v, v, ...]` initializer for an array field."""

    def __init__(self, items: Sequence[InitItem], file_info: Token, close_token: Token | None = None) -> None:
        super().__init__("list_init", file_info)
        self.items = tuple(items)
        self.close_token = close_token

    @property
    def values(self) -> list[InitValue]:
        return [item for item in self.items if not isinstance(item, InitCommentAstNode)]

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, [value.to_representation() for value in self.values]

    def to_canonical(self) -> str:
        return "\n".join(render_value(self))


class StructFieldInitAstNode(AstNode):
    """One `name = value` entry; ``file_info`` is the field-name token."""

    def __init__(self, name: str, value: InitValue, file_info: Token) -> None:
        super().__init__("field_init", file_info)
        self.name = name
        self.value = value

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.name, self.value.to_representation()

    def to_canonical(self) -> str:
        return "\n".join(_render_field(self))


class StructInitAstNode(AstNode):
    """`{ name = value, ... }` initializer for a struct (or nested struct field)."""

    def __init__(self, items: Sequence[StructInitItem], file_info: Token, close_token: Token | None = None) -> None:
        super().__init__("struct_init", file_info)
        self.items = tuple(items)
        self.close_token = close_token

    @property
    def fields(self) -> list[StructFieldInitAstNode]:
        return [item for item in self.items if isinstance(item, StructFieldInitAstNode)]

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, [entry.to_representation() for entry in self.fields]

    def to_canonical(self) -> str:
        return "\n".join(render_value(self))


InitValue = ExpressionAstNode | StringInitAstNode | ListInitAstNode | StructInitAstNode
InitItem = InitValue | InitCommentAstNode
StructInitItem = StructFieldInitAstNode | InitCommentAstNode
_Item = InitItem | StructInitItem


class StructInstanceAstNode(AstNode):
    """`.istruct TYPE { ... }`: one instance of TYPE laid out as bytes."""

    def __init__(self, type_name: str, init: StructInitAstNode, type_token: Token, file_info: Token) -> None:
        super().__init__("istruct", file_info)
        self.type_name = type_name
        self.type_token = type_token
        self.init = init
        # Exposed under the formatter's child-walk attributes so the node's
        # source extent covers every initializer line up to the closing `}`.
        self.items = init.items
        self.close_token = init.close_token

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.type_name, self.init.to_representation()

    def to_canonical(self) -> str:
        if not self.items:
            return f".istruct {self.type_name} {{}}"
        body = _render_items(self.items, separator="")
        return "\n".join([f".istruct {self.type_name} {{", *body, "}"])


def _has_comment(value: InitValue) -> bool:
    """True when `value` (or anything nested in it) carries a comment."""
    if isinstance(value, StructInitAstNode):
        return any(_item_has_comment(item) for item in value.items)
    if isinstance(value, ListInitAstNode):
        return any(_item_has_comment(item) for item in value.items)
    return False


def _item_has_comment(item: _Item) -> bool:
    if isinstance(item, InitCommentAstNode):
        return True
    if isinstance(item, StructFieldInitAstNode):
        return _has_comment(item.value)
    return _has_comment(item)


def render_value(value: InitValue) -> list[str]:
    """Render a value on one line, or as a block when it holds comments
    or the author spread it over several lines."""
    if isinstance(value, StructInitAstNode):
        return _render_aggregate(value, "{", "}", separator="")
    if isinstance(value, ListInitAstNode):
        return _render_aggregate(value, "[", "]", separator=",")
    return [value.to_canonical()]


def _spans_lines(value: StructInitAstNode | ListInitAstNode) -> bool:
    opened = value.file_info.position
    closed = value.close_token.position if value.close_token is not None else None
    return opened is not None and closed is not None and opened.line != closed.line


def _render_aggregate(
    value: StructInitAstNode | ListInitAstNode, opener: str, closer: str, separator: str
) -> list[str]:
    items: Sequence[_Item] = value.items
    entries = [item for item in items if not isinstance(item, InitCommentAstNode)]
    if not _spans_lines(value) and not any(_item_has_comment(item) for item in items):
        inline = ", ".join(_render_entry(entry)[0] for entry in entries)
        if opener == "{":
            return [f"{{ {inline} }}"] if inline else ["{}"]
        return [f"[{inline}]"]
    return [opener, *_render_items(items, separator), closer]


def _render_entry(entry: StructFieldInitAstNode | InitValue) -> list[str]:
    if isinstance(entry, StructFieldInitAstNode):
        return _render_field(entry)
    return render_value(entry)


def _render_field(entry: StructFieldInitAstNode) -> list[str]:
    value_lines = render_value(entry.value)
    return [f"{entry.name} = {value_lines[0]}", *value_lines[1:]]


def _render_items(items: Sequence[_Item], separator: str) -> list[str]:
    """One indented line per entry; trailing comments ride on the line above."""
    lines: list[str] = []
    entry_count = sum(1 for item in items if not isinstance(item, InitCommentAstNode))
    seen = 0
    for item in items:
        if isinstance(item, InitCommentAstNode):
            if item.trailing and lines:
                lines[-1] = f"{lines[-1]} {item.comment.strip()}"
            else:
                lines.append(f"{_INDENT}{item.comment.strip()}")
            continue
        seen += 1
        entry_lines = _render_entry(item)
        if seen < entry_count:
            entry_lines[-1] += separator
        lines.extend(f"{_INDENT}{line}" for line in entry_lines)
    return lines


def value_expressions(value: InitValue) -> Iterator[ExpressionAstNode]:
    """Every expression inside an initializer value, depth first."""
    if isinstance(value, ExpressionAstNode):
        yield value
    elif isinstance(value, StructInitAstNode):
        for entry in value.fields:
            yield from value_expressions(entry.value)
    elif isinstance(value, ListInitAstNode):
        for item in value.values:
            yield from value_expressions(item)
