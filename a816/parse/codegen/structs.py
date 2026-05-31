"""Struct field layout, bit-field packing, `.struct` + `.map` emitters."""

from __future__ import annotations

import re

from a816.parse.ast.nodes import MapAstNode, StructAstNode
from a816.parse.codegen.base import GenNodes, MacroDefinitions, generators
from a816.parse.nodes import NodeError, PopScopeNode, ScopeNode
from a816.parse.tokens import Token
from a816.protocols import NodeProtocol
from a816.symbols import Resolver

# Byte sizes per declared struct field type. dword is 4 because users who
# write it mean 32-bit; 65c816 effective addresses fit in 24 (use `long`).
_STRUCT_FIELD_SIZES = {"byte": 1, "word": 2, "long": 3, "dword": 4}

# Bit-field types are spelled `uN` for any positive `N`. The width travels
# in the type name itself so the parser keeps the simple `type name` shape
# with no new tokens.
_BIT_FIELD_TYPE_RE = re.compile(r"u(\d+)$")


def _bit_width_from_type(field_type: str) -> int | None:
    """Return the bit width when `field_type` matches `uN`, else None."""
    match = _BIT_FIELD_TYPE_RE.fullmatch(field_type)
    if match is None:
        return None
    width = int(match.group(1))
    if width < 1:
        return None
    return width


def _layout_struct_fields(
    node: StructAstNode,
    resolver: Resolver,
    file_info: Token,
) -> tuple[list[tuple[str, int, int]], int, dict[str, tuple[int, int]]]:
    """Compute flat (field_path, offset, width) entries + total size for a struct.

    Primitive fields contribute one entry whose ``width`` is the declared
    type's byte size (1/2/3/4). Nested struct fields contribute the parent
    field at its own offset with width equal to the nested struct's
    ``__size`` plus every flattened sub-entry inheriting its declared
    primitive width. Forward refs and self-references raise a NodeError.

    Width is what `lda p.field` needs to pick the right operand encoding
    later; without it auto-sizing would have to fall back to the string
    heuristic that already misfires for typed accesses.
    """
    entries: list[tuple[str, int, int]] = []
    bit_meta: dict[str, tuple[int, int]] = {}
    bit_buffer: list[tuple[str, int, int]] = []
    offset = 0
    bit_position = 0

    for field_name, field_type in node.fields:
        bit_width = _bit_width_from_type(field_type)
        if bit_width is not None:
            bit_buffer.append((field_name, bit_position, bit_width))
            bit_position += bit_width
            continue
        if bit_buffer:
            offset += _flush_bit_run(bit_buffer, entries, bit_meta, offset)
            bit_buffer = []
            bit_position = 0
        primitive_size = _STRUCT_FIELD_SIZES.get(field_type)
        if primitive_size is not None:
            entries.append((field_name, offset, primitive_size))
            offset += primitive_size
            continue
        if field_type == node.name:
            raise NodeError(
                f"Struct {node.name!r} field {field_name!r} cannot reference its own type.",
                file_info,
            )
        if field_type not in resolver.struct_layouts:
            raise NodeError(
                f"Unknown struct field type {field_type!r} for {node.name}.{field_name}; "
                f"declare `.struct {field_type}` before use.",
                file_info,
            )
        nested_layout = resolver.struct_layouts[field_type]
        nested_size = resolver.struct_sizes[field_type]
        entries.append((field_name, offset, nested_size))
        for sub_path, sub_offset, sub_width in nested_layout:
            entries.append((f"{field_name}.{sub_path}", offset + sub_offset, sub_width))
        offset += nested_size
    if bit_buffer:
        offset += _flush_bit_run(bit_buffer, entries, bit_meta, offset)
    return entries, offset, bit_meta


def _flush_bit_run(
    bit_buffer: list[tuple[str, int, int]],
    entries: list[tuple[str, int, int]],
    bit_meta: dict[str, tuple[int, int]],
    byte_offset: int,
) -> int:
    """Pack a run of bit fields into byte(s) starting at `byte_offset`.

    For each field, appends one byte-offset entry (so typed-bind
    expansion produces an absolute address for the containing byte) and
    records `(mask, shift)` in `bit_meta` for the caller to publish as
    flat constants alongside the offset symbols.

    Returns the number of bytes consumed by the packed run.
    """
    total_bits = bit_buffer[-1][1] + bit_buffer[-1][2]
    bytes_used = (total_bits + 7) // 8
    for name, lsb, width in bit_buffer:
        entries.append((name, byte_offset + lsb // 8, 1))
        bit_in_byte = lsb % 8
        mask = ((1 << width) - 1) << bit_in_byte
        bit_meta[name] = (mask, bit_in_byte)
    return bytes_used


def generate_struct(
    node: StructAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    """Push a NamedScope, register one offset symbol per (possibly nested)
    field, register the layout for later typed-bind expansion, then export.

    Idempotent: a second `.struct` with the same name + identical field list
    is treated as a no-op so a header `.include`d twice (or imported via
    different cascades) doesn't fail. A mismatched redef still raises so
    real layout bugs surface.
    """
    entries, total_size, bit_meta = _layout_struct_fields(node, resolver, file_info)
    existing = resolver.struct_layouts.get(node.name)
    if existing is not None:
        if (
            existing == entries
            and resolver.struct_sizes.get(node.name) == total_size
            and resolver.struct_bitfields.get(node.name, {}) == bit_meta
        ):
            return []
        raise NodeError(
            f"Struct {node.name!r} redefined with a different field layout.",
            file_info,
        )
    resolver.struct_layouts[node.name] = entries
    resolver.struct_sizes[node.name] = total_size
    if bit_meta:
        resolver.struct_bitfields[node.name] = bit_meta

    resolver.append_named_scope(node.name)
    resolver.use_next_scope()
    code: list[NodeProtocol] = [ScopeNode(resolver)]
    for field_path, offset, _width in entries:
        resolver.current_scope.add_symbol(field_path, offset)
    # Bit-field mask + shift are absolute constants — they go into the
    # struct's scope as flat symbols and DO NOT belong in the layout list
    # (which the typed-bind eager-expansion path shifts by the instance
    # base).
    for field_name, (mask, shift) in bit_meta.items():
        resolver.current_scope.add_symbol(f"{field_name}.mask", mask)
        resolver.current_scope.add_symbol(f"{field_name}.shift", shift)
    resolver.current_scope.add_symbol("__size", total_size)
    # exports=True promotes Name.field and Name.__size to the parent scope.
    code.append(PopScopeNode(resolver, exports=True))
    resolver.restore_scope(exports=True)
    return code


def generate_map(
    node: MapAstNode,
    resolver: Resolver,
    macro_definitions: MacroDefinitions,
    file_info: Token,
) -> GenNodes:
    attributes = node.args

    identifier = str(attributes["identifier"])
    bank_range = attributes["bank_range"]
    addr_range = attributes["addr_range"]
    mask = attributes["mask"]
    writeable = attributes.get("writable", False)
    mirror_bank_range = attributes.get("mirror_bank_range")

    resolver.bus.map(
        identifier,
        bank_range,
        addr_range,
        mask,
        writeable=writeable,
        mirror_bank_range=mirror_bank_range,
    )
    # OBJECT mode: serialize so the linker replays the mapping on its
    # own resolver bus. Without this, custom cartridge mappings
    # (SA-1, ExHiROM, anything beyond the default low_rom) silently
    # vanish at link time and downstream addresses resolve wrong.
    if resolver.context.is_object_mode and resolver.context.object_writer is not None:
        from a816.object_file import BusMapping

        resolver.context.object_writer.bus_mappings.append(
            BusMapping(
                identifier=identifier,
                bank_range=bank_range,
                addr_range=addr_range,
                mask=mask,
                writeable=writeable,
                mirror_bank_range=mirror_bank_range,
            )
        )
    return []


generators["struct"] = generate_struct
generators["map"] = generate_map
