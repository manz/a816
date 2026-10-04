"""`.istruct T { ... }`: lay a struct instance out as data nodes.

Fields emit in declaration order; unset fields are zero-filled. Values go
through the same sized data nodes as `.db` / `.dw` / `.dl`, so they mask
to the field width and an extern expression becomes an expression
relocation in object mode.
"""

from __future__ import annotations

from itertools import groupby

from a816.error_codes import (
    E_CODEGEN_ISTRUCT_BIT_RUN_TOO_WIDE,
    E_CODEGEN_ISTRUCT_NON_ASCII,
    E_CODEGEN_ISTRUCT_TOO_LONG,
    E_CODEGEN_ISTRUCT_UNKNOWN_FIELD,
    E_CODEGEN_ISTRUCT_UNKNOWN_TYPE,
    E_CODEGEN_ISTRUCT_VALUE_KIND,
)
from a816.parse.ast.nodes import (
    BinOp,
    ExpressionAstNode,
    ExprNode,
    InitValue,
    ListInitAstNode,
    Parenthesis,
    StringInitAstNode,
    StructFieldInitAstNode,
    StructInitAstNode,
    StructInstanceAstNode,
    Term,
)
from a816.parse.codegen.base import GenNodes, MacroDefinitions, generators
from a816.parse.codegen.structs import STRUCT_FIELD_SIZES, bit_width_from_type, split_array_type
from a816.parse.nodes import (
    ByteNode,
    BytesNode,
    DwordNode,
    ExpressionNode,
    LongNode,
    NodeError,
    WordNode,
)
from a816.parse.tokens import Token, TokenType
from a816.protocols import NodeProtocol
from a816.symbols import Resolver

_SIZED_NODES: dict[int, type[ByteNode | WordNode | LongNode | DwordNode]] = {
    1: ByteNode,
    2: WordNode,
    3: LongNode,
    4: DwordNode,
}

_Field = tuple[str, str]


def _value_kind_error(value: InitValue, expected: str) -> NodeError:
    return NodeError(
        f"expected {expected} here",
        value.file_info,
        code=str(E_CODEGEN_ISTRUCT_VALUE_KIND),
        hint="scalars take an expression, byte arrays a string or `[...]`, "
        "other arrays `[...]`, struct fields `{ ... }`",
    )


def _too_long_error(value: InitValue, length: int, count: int) -> NodeError:
    return NodeError(
        f"initializer has {length} elements but the array holds {count}",
        value.file_info,
        code=str(E_CODEGEN_ISTRUCT_TOO_LONG),
    )


def _zeros(size: int) -> list[NodeProtocol]:
    return [BytesNode(bytes(size))] if size else []


def _encode_string(value: StringInitAstNode) -> bytes:
    try:
        return value.text.encode("ascii")
    except UnicodeEncodeError as e:
        raise NodeError(
            f"string initializer {value.file_info.value} is not ASCII",
            value.file_info,
            code=str(E_CODEGEN_ISTRUCT_NON_ASCII),
            hint="use a `[...]` list of byte values for other encodings",
        ) from e


def _expression_token(kind: TokenType, text: str, anchor: Token) -> Token:
    return Token(kind, text, anchor.position)


class _InstanceEmitter:
    """Walk a struct's declared fields, turning an initializer into data nodes."""

    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver

    def struct(self, type_name: str, init: StructInitAstNode | None) -> list[NodeProtocol]:
        fields = self.resolver.struct_fields[type_name]
        entries = {entry.name: entry for entry in init.fields} if init is not None else {}
        declared = {name for name, _type in fields}
        for entry in entries.values():
            if entry.name not in declared:
                raise NodeError(
                    f"struct {type_name!r} has no field {entry.name!r}",
                    entry.file_info,
                    code=str(E_CODEGEN_ISTRUCT_UNKNOWN_FIELD),
                    hint=f"fields of {type_name}: {', '.join(name for name, _type in fields)}",
                )
        code: list[NodeProtocol] = []
        for is_bit_run, group in groupby(fields, key=lambda f: bit_width_from_type(f[1]) is not None):
            run = list(group)
            if is_bit_run:
                code.extend(self._bit_run(run, entries))
                continue
            for name, field_type in run:
                code.extend(self._field(field_type, entries.get(name)))
        return code

    def _field(self, field_type: str, entry: StructFieldInitAstNode | None) -> list[NodeProtocol]:
        element_type, count = split_array_type(field_type)
        value = entry.value if entry is not None else None
        if count is None:
            return self._element(element_type, value)
        return self._array(element_type, count, value)

    def _element_size(self, element_type: str) -> int:
        primitive = STRUCT_FIELD_SIZES.get(element_type)
        return primitive if primitive is not None else self.resolver.struct_sizes[element_type]

    def _element(self, element_type: str, value: InitValue | None) -> list[NodeProtocol]:
        """One scalar or nested-struct element; `None` zero-fills it."""
        if value is None:
            return _zeros(self._element_size(element_type))
        size = STRUCT_FIELD_SIZES.get(element_type)
        if size is None:
            if not isinstance(value, StructInitAstNode):
                raise _value_kind_error(value, f"a `{{ ... }}` initializer for struct {element_type!r}")
            return self.struct(element_type, value)
        if not isinstance(value, ExpressionAstNode):
            raise _value_kind_error(value, f"an expression for a `{element_type}` field")
        return [self._sized(size, value)]

    def _array(self, element_type: str, count: int, value: InitValue | None) -> list[NodeProtocol]:
        element_size = self._element_size(element_type)
        if value is None:
            return _zeros(element_size * count)
        if isinstance(value, StringInitAstNode) and element_type == "byte":
            data = _encode_string(value)
            if len(data) > count:
                raise _too_long_error(value, len(data), count)
            return [BytesNode(data + bytes(count - len(data)))]
        if not isinstance(value, ListInitAstNode):
            raise _value_kind_error(value, f"a `[...]` list for a `{element_type}[{count}]` field")
        items = value.values
        if len(items) > count:
            raise _too_long_error(value, len(items), count)
        code: list[NodeProtocol] = []
        for item in items:
            code.extend(self._element(element_type, item))
        code.extend(_zeros(element_size * (count - len(items))))
        return code

    def _sized(self, size: int, expression: ExpressionAstNode) -> NodeProtocol:
        return _SIZED_NODES[size](ExpressionNode(expression, self.resolver, expression.file_info))

    def _bit_run(self, run: list[_Field], entries: dict[str, StructFieldInitAstNode]) -> list[NodeProtocol]:
        """Pack a run of `uN` fields into one little-endian value."""
        widths = [(name, bit_width_from_type(field_type) or 0) for name, field_type in run]
        size = (sum(width for _name, width in widths) + 7) // 8
        terms: list[list[ExprNode]] = []
        lsb = 0
        for name, width in widths:
            entry = entries.get(name)
            if entry is not None:
                terms.append(self._bit_term(entry, width, lsb))
            lsb += width
        if not terms:
            return _zeros(size)
        if size > 4:
            first = next(entries[name] for name, _width in widths if name in entries)
            raise NodeError(
                f"bit-field run of {size} bytes is too wide to initialize",
                first.file_info,
                code=str(E_CODEGEN_ISTRUCT_BIT_RUN_TOO_WIDE),
                hint="split the run with a byte-aligned field so each run fits in 32 bits",
            )
        tokens = terms[0]
        for term in terms[1:]:
            tokens = [*tokens, BinOp(_expression_token(TokenType.OPERATOR, "|", term[0].token)), *term]
        return [self._sized(size, ExpressionAstNode(tokens))]

    @staticmethod
    def _bit_term(entry: StructFieldInitAstNode, width: int, lsb: int) -> list[ExprNode]:
        """`((value & mask) << lsb)` as expression tokens anchored on the field."""
        value = entry.value
        if not isinstance(value, ExpressionAstNode):
            raise _value_kind_error(value, f"an expression for the `u{width}` field {entry.name!r}")
        anchor = entry.file_info

        def tok(kind: TokenType, text: str) -> Token:
            return _expression_token(kind, text, anchor)

        return [
            Parenthesis(tok(TokenType.LPAREN, "(")),
            Parenthesis(tok(TokenType.LPAREN, "(")),
            Parenthesis(tok(TokenType.LPAREN, "(")),
            *value.tokens,
            Parenthesis(tok(TokenType.RPAREN, ")")),
            BinOp(tok(TokenType.OPERATOR, "&")),
            Term(tok(TokenType.NUMBER, hex((1 << width) - 1))),
            Parenthesis(tok(TokenType.RPAREN, ")")),
            BinOp(tok(TokenType.OPERATOR, "<<")),
            Term(tok(TokenType.NUMBER, str(lsb))),
            Parenthesis(tok(TokenType.RPAREN, ")")),
        ]


def generate_istruct(
    node: StructInstanceAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    if node.type_name not in resolver.struct_fields:
        raise NodeError(
            f"`.istruct` of unknown struct type {node.type_name!r}",
            node.type_token,
            code=str(E_CODEGEN_ISTRUCT_UNKNOWN_TYPE),
            hint=f"declare `.struct {node.type_name} {{ ... }}` (or import it) first",
        )
    return _InstanceEmitter(resolver).struct(node.type_name, node.init)


generators["istruct"] = generate_istruct
