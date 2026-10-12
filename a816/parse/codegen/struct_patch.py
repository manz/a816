"""`.patch TYPE at ADDR { ... }`: write the given fields of a TYPE at ADDR.

Each run of contiguous given fields becomes an anonymous pinned alloc at
`ADDR + offset`, so the fields left out keep the bytes the ROM has, and the
pinned-block overlap check (E0408) covers a patch landing on another block.
Values encode as in `.istruct` (`InstanceEmitter`), without the zero-fill.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from itertools import groupby
from typing import Any

from a816.error_codes import (
    E_CODEGEN_ISTRUCT_UNKNOWN_FIELD,
    E_CODEGEN_ISTRUCT_UNKNOWN_TYPE,
    E_CODEGEN_PATCH_PARTIAL_BITS,
)
from a816.parse.ast.nodes import (
    AllocAstNode,
    AstNode,
    BinOp,
    BlockAstNode,
    ExpressionAstNode,
    InitValue,
    ListInitAstNode,
    Parenthesis,
    StringInitAstNode,
    StructFieldInitAstNode,
    StructInitAstNode,
    StructPatchAstNode,
    Term,
)
from a816.parse.codegen.base import GenNodes, MacroDefinitions, generators
from a816.parse.codegen.pool import generate_alloc
from a816.parse.codegen.struct_instance import InstanceEmitter, encode_string, too_long_error, value_kind_error
from a816.parse.codegen.structs import STRUCT_FIELD_SIZES, bit_width_from_type, split_array_type
from a816.parse.nodes import BytesNode, NodeError
from a816.parse.tokens import Token, TokenType
from a816.protocols import NodeProtocol
from a816.symbols import Resolver


@dataclass(frozen=True)
class _Leaf:
    """One piece of a patch at a struct offset: a scalar (`element_type` +
    `value`), a whole bit-field run (`bits`), or a string's bytes (`data`)."""

    offset: int
    size: int
    anchor: Token
    element_type: str = ""
    value: InitValue | None = None
    bits: tuple[tuple[str, str], ...] = ()
    entries: tuple[StructFieldInitAstNode, ...] = ()
    data: bytes = b""


class PatchChunkAstNode(AstNode):
    """Codegen-made body of one patch block: its leaves, back to back."""

    def __init__(self, leaves: list[_Leaf], file_info: Token) -> None:
        super().__init__("patch_chunk", file_info)
        self.leaves = tuple(leaves)

    def to_representation(self) -> tuple[Any, ...]:
        return self.kind, [(leaf.offset, leaf.size) for leaf in self.leaves]


class _Planner:
    """Walk a patch initializer into leaves at their offsets in the type."""

    def __init__(self, resolver: Resolver) -> None:
        self.resolver = resolver
        self.emitter = InstanceEmitter(resolver)

    def struct(self, type_name: str, init: StructInitAstNode, base: int) -> Iterator[_Leaf]:
        fields = self.resolver.struct_fields[type_name]
        offsets = {path: offset for path, offset, _width in self.resolver.struct_layouts[type_name] if "." not in path}
        entries = {entry.name: entry for entry in init.fields}
        _reject_unknown_fields(type_name, fields, entries)
        for is_bit_run, group in groupby(fields, key=lambda f: bit_width_from_type(f[1]) is not None):
            run = list(group)
            if is_bit_run:
                yield from self._bit_run(run, entries, base + offsets[run[0][0]])
                continue
            for name, field_type in run:
                if name in entries:
                    yield from self._field(field_type, entries[name], base + offsets[name])

    def _bit_run(
        self, run: list[tuple[str, str]], entries: dict[str, StructFieldInitAstNode], offset: int
    ) -> Iterator[_Leaf]:
        given = [entries[name] for name, _type in run if name in entries]
        if not given:
            return
        missing = [name for name, _type in run if name not in entries]
        if missing:
            raise NodeError(
                f"`.patch` sets part of a bit-field byte: {', '.join(f'`{name}`' for name in missing)} share it",
                given[0].file_info,
                code=str(E_CODEGEN_PATCH_PARTIAL_BITS),
                hint="a816 never sees the ROM's byte, so give every field of the run or patch it as a whole",
            )
        size = (sum(bit_width_from_type(field_type) or 0 for _name, field_type in run) + 7) // 8
        yield _Leaf(offset, size, given[0].file_info, bits=tuple(run), entries=tuple(given))

    def _field(self, field_type: str, entry: StructFieldInitAstNode, offset: int) -> Iterator[_Leaf]:
        element_type, count = split_array_type(field_type)
        if count is None:
            yield from self._element(element_type, entry.value, offset, entry.file_info)
            return
        value = entry.value
        if isinstance(value, StringInitAstNode) and element_type == "byte":
            data = encode_string(value)
            if len(data) > count:
                raise too_long_error(value, len(data), count)
            if data:
                yield _Leaf(offset, len(data), entry.file_info, data=data)
            return
        if not isinstance(value, ListInitAstNode):
            raise value_kind_error(value, f"a `[...]` list for a `{element_type}[{count}]` field")
        if len(value.values) > count:
            raise too_long_error(value, len(value.values), count)
        element_size = self.emitter.element_size(element_type)
        for index, item in enumerate(value.values):
            yield from self._element(element_type, item, offset + index * element_size, entry.file_info)

    def _element(self, element_type: str, value: InitValue, offset: int, anchor: Token) -> Iterator[_Leaf]:
        size = STRUCT_FIELD_SIZES.get(element_type)
        if size is not None:
            if not isinstance(value, ExpressionAstNode):
                raise value_kind_error(value, f"an expression for a `{element_type}` field")
            yield _Leaf(offset, size, anchor, element_type=element_type, value=value)
            return
        if not isinstance(value, StructInitAstNode):
            raise value_kind_error(value, f"a `{{ ... }}` initializer for struct {element_type!r}")
        yield from self.struct(element_type, value, offset)


def _reject_unknown_fields(
    type_name: str, fields: list[tuple[str, str]], entries: dict[str, StructFieldInitAstNode]
) -> None:
    declared = {name for name, _type in fields}
    for entry in entries.values():
        if entry.name not in declared:
            raise NodeError(
                f"struct {type_name!r} has no field {entry.name!r}",
                entry.file_info,
                code=str(E_CODEGEN_ISTRUCT_UNKNOWN_FIELD),
                hint=f"fields of {type_name}: {', '.join(name for name, _type in fields)}",
            )


def _blocks(leaves: list[_Leaf]) -> list[list[_Leaf]]:
    """Leaves grouped into runs of touching offsets, in address order."""
    blocks: list[list[_Leaf]] = []
    for leaf in sorted(leaves, key=lambda leaf: leaf.offset):
        last = blocks[-1][-1] if blocks else None
        if last is not None and last.offset + last.size == leaf.offset:
            blocks[-1].append(leaf)
        else:
            blocks.append([leaf])
    return blocks


def _number(value: int, anchor: Token) -> ExpressionAstNode:
    return ExpressionAstNode([Term(Token(TokenType.NUMBER, hex(value), anchor.position))])


def _offset_from(address: ExpressionAstNode, offset: int, anchor: Token) -> ExpressionAstNode:
    """`(ADDR) + offset`, evaluated by the alloc like any `.alloc at`."""
    position = anchor.position
    return ExpressionAstNode(
        [
            Parenthesis(Token(TokenType.LPAREN, "(", position)),
            *address.tokens,
            Parenthesis(Token(TokenType.RPAREN, ")", position)),
            BinOp(Token(TokenType.OPERATOR, "+", position)),
            Term(Token(TokenType.NUMBER, hex(offset), position)),
        ]
    )


def generate_patch(
    node: StructPatchAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    if node.type_name not in resolver.struct_fields:
        raise NodeError(
            f"`.patch` of unknown struct type {node.type_name!r}",
            node.type_token,
            code=str(E_CODEGEN_ISTRUCT_UNKNOWN_TYPE),
            hint=f"declare `.struct {node.type_name} {{ ... }}` (or import it) first",
        )
    code: GenNodes = []
    for block in _blocks(list(_Planner(resolver).struct(node.type_name, node.init, 0))):
        anchor = block[0].anchor
        size = block[-1].offset + block[-1].size - block[0].offset
        body = BlockAstNode([PatchChunkAstNode(block, anchor)], anchor)
        alloc = AllocAstNode(
            None,
            None,
            body,
            anchor,
            at_address=_offset_from(node.address, block[0].offset, anchor),
            at_size=_number(size, anchor),
        )
        code.extend(generate_alloc(alloc, resolver, macro_definitions, anchor))
    return code


def generate_patch_chunk(
    node: PatchChunkAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    emitter = InstanceEmitter(resolver)
    code: list[NodeProtocol] = []
    for leaf in node.leaves:
        if leaf.bits:
            code.extend(emitter.bit_run(list(leaf.bits), {entry.name: entry for entry in leaf.entries}))
        elif leaf.data:
            code.append(BytesNode(leaf.data))
        else:
            assert leaf.value is not None
            code.extend(emitter.element(leaf.element_type, leaf.value))
    return code


generators["patch"] = generate_patch
generators["patch_chunk"] = generate_patch_chunk
