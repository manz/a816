"""Text / data / include / table / map / register-size / position directives."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from a816.parse.ast.nodes.base import AstNode, ExpressionAstNode
from a816.parse.ast.nodes.containers import BlockAstNode
from a816.parse.tokens import Token


class TextAstNode(AstNode):
    text: str

    def __init__(self, text: str, file_info: Token) -> None:
        super().__init__("text", file_info)
        self.text = text

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.text

    def to_canonical(self) -> str:
        return f'.text "{self.text}"'


class AsciiAstNode(AstNode):
    text: str

    def __init__(self, text: str, file_info: Token) -> None:
        super().__init__("ascii", file_info)
        self.text = text

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.text

    def to_canonical(self) -> str:
        return f'.ascii "{self.text}"'


class CommentAstNode(AstNode):
    comment: str

    def __init__(self, comment: str, file_info: Token) -> None:
        super().__init__("comment", file_info)
        self.comment = comment

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.comment

    def to_canonical(self) -> str:
        return self.comment


class DocstringAstNode(AstNode):
    def __init__(self, text: str, file_info: Token) -> None:
        super().__init__("docstring", file_info)
        # Raw inner content as it appeared between the triple quotes —
        # ruff-preview-style: the formatter reindents but never edits the
        # text. Consumers wanting normalized prose call `inspect.cleandoc`.
        self.text = text

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.text

    def to_canonical(self) -> str:
        if '"""' in self.text and "'''" not in self.text:
            return f"'''{self.text}'''"
        if '"""' in self.text:
            escaped = self.text.replace('"""', '\\"""')
            return f'"""{escaped}"""'
        return f'"""{self.text}"""'


class CodePositionAstNode(AstNode):
    def __init__(self, expression: ExpressionAstNode, file_info: Token):
        super().__init__("star_eq", file_info)
        self.expression = expression

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.expression.to_representation()[0]

    def to_canonical(self) -> str:
        return f"*={self.expression.to_canonical()}"


class CodeRelocationAstNode(AstNode):
    def __init__(self, expression: ExpressionAstNode, file_info: Token):
        super().__init__("at_eq", file_info)
        self.expression = expression

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.expression.to_representation()[0]

    def to_canonical(self) -> str:
        return f"@={self.expression.to_canonical()}"


class MapArgs(TypedDict, total=False):
    identifier: str | int
    writable: bool
    bank_range: tuple[int, int]
    addr_range: tuple[int, int]
    mask: int
    mirror_bank_range: tuple[int, int]


_MAP_FIELD_WIDTH: dict[str, int] = {
    "bank_range": 2,
    "addr_range": 4,
    "mask": 4,
    "mirror_bank_range": 2,
}


def _map_value_repr(key: str, value: int) -> str:
    """Format a single .map value back to assembler syntax. Keeps
    `identifier` and `writable` as bare integers (matching how the
    parser stores them) and uses zero-padded lowercase hex for the
    address / mask fields so the output round-trips through the
    parser at the same widths the source used.
    """
    if key in ("identifier", "writable"):
        return str(value)
    width = _MAP_FIELD_WIDTH.get(key, 4)
    return f"0x{value:0{width}x}"


class MapAstNode(AstNode):
    args: MapArgs

    _FIELD_ORDER: tuple[str, ...] = (
        "identifier",
        "bank_range",
        "addr_range",
        "mask",
        "mirror_bank_range",
        "writable",
    )

    def __init__(self, args: MapArgs, file_info: Token):
        super().__init__("map", file_info)
        self.args = args

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.args

    def to_canonical(self) -> str:
        parts: list[str] = [".map"]
        items: dict[str, Any] = dict(self.args)
        for key in self._FIELD_ORDER:
            if key not in items:
                continue
            value = items[key]
            if isinstance(value, tuple):
                low, high = value
                parts.append(f"{key}={_map_value_repr(key, low)}, {_map_value_repr(key, high)}")
            else:
                parts.append(f"{key}={_map_value_repr(key, int(value))}")
        return " ".join(parts)


class DataNode(AstNode):
    data: list[ExpressionAstNode]

    def __init__(
        self,
        kind: str,
        data: list[ExpressionAstNode | BlockAstNode],
        file_info: Token,
    ):
        super().__init__(kind, file_info)

        self.data = []

        for d in data:
            assert isinstance(d, ExpressionAstNode)
            self.data.append(d)

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, [d.to_representation()[0] for d in self.data]

    def to_canonical(self) -> str:
        values = [d.to_canonical() for d in self.data]
        return f".{self.kind} {', '.join(values)}"


class TableAstNode(AstNode):
    file_path: str

    def __init__(self, file_path: str, file_info: Token):
        super().__init__("table", file_info)
        self.file_path = file_path

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.file_path

    def to_canonical(self) -> str:
        return f'.table "{self.file_path}"'


class IncludeAstNode(AstNode):
    file_path: str
    included_nodes: list[AstNode]

    def __init__(self, file_path: str, included_nodes: list[AstNode], file_info: Token):
        """Represents a source-level .include directive while preserving the nested AST."""
        super().__init__("include", file_info)
        self.file_path = file_path
        self.included_nodes = included_nodes

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.file_path, [node.to_representation() for node in self.included_nodes]

    def to_canonical(self) -> str:
        return f'.include "{self.file_path}"'


class IncludeIpsAstNode(AstNode):
    file_path: str

    def __init__(self, file_path: str, expression: ExpressionAstNode, file_info: Token):
        super().__init__("include_ips", file_info)
        self.file_path = file_path
        self.expression = expression

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.file_path, self.expression.to_representation()[0]

    def to_canonical(self) -> str:
        expr = self.expression.to_canonical()
        return f'.include_ips "{self.file_path}" {expr}'


class IncludeBinaryAstNode(AstNode):
    file_path: str

    def __init__(self, file_path: str, file_info: Token):
        super().__init__("incbin", file_info)
        self.file_path = file_path

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.file_path

    def to_canonical(self) -> str:
        return f'.incbin "{self.file_path}"'


class DebugAstNode(AstNode):
    def __init__(self, message: str, file_info: Token) -> None:
        super().__init__("debug", file_info)
        self.message = message

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.message

    def to_canonical(self) -> str:
        return f".debug '{self.message}'"


class RegisterSizeAstNode(AstNode):
    """AST node for register size directives (.a8, .a16, .i8, .i16)"""

    def __init__(self, register: str, size: int, file_info: Token) -> None:
        super().__init__("register_size", file_info)
        self.register = register  # "a" for accumulator, "i" for index
        self.size = size  # 8 or 16

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, self.register, self.size

    def to_canonical(self) -> str:
        return f".{self.register}{self.size}"


FileInfoAstNode = tuple[Literal["file_info"], Token]
