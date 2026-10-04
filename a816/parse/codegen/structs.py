"""Struct field layout, bit-field packing, `.struct` + `.map` emitters."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from a816.context import AssemblyMode
from a816.cpu.mapping import Bus
from a816.error_codes import E_CODEGEN_MAP_CONFLICT
from a816.object_file import BusMapping
from a816.parse.ast.expression import eval_number
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

# Array fields carry their element count in the type string: `byte[21]`,
# `Pt[0x03]`. The parser has already validated the count literal.
_ARRAY_TYPE_RE = re.compile(r"(\w+)\[(\w+)\]", re.ASCII)


def _bit_width_from_type(field_type: str) -> int | None:
    """Return the bit width when `field_type` matches `uN`, else None."""
    match = _BIT_FIELD_TYPE_RE.fullmatch(field_type)
    if match is None:
        return None
    width = int(match.group(1))
    if width < 1:
        return None
    return width


@dataclass
class _StructLayout:
    """Accumulator for one struct's flattened layout.

    ``entries`` holds `(field_path, offset, width)`; ``bit_meta`` the
    `(mask, shift)` of each bit field; ``array_sizes`` the total byte size
    of every (possibly nested) array field, published as `.__size`.
    """

    entries: list[tuple[str, int, int]] = field(default_factory=list)
    bit_meta: dict[str, tuple[int, int]] = field(default_factory=dict)
    array_sizes: dict[str, int] = field(default_factory=dict)
    offset: int = 0
    bit_buffer: list[tuple[str, int, int]] = field(default_factory=list)
    bit_position: int = 0

    def add_bit_field(self, name: str, width: int) -> None:
        self.bit_buffer.append((name, self.bit_position, width))
        self.bit_position += width

    def flush_bits(self) -> None:
        if self.bit_buffer:
            self.offset += _flush_bit_run(self.bit_buffer, self.entries, self.bit_meta, self.offset)
            self.bit_buffer = []
            self.bit_position = 0

    def add_field(self, name: str, element: _ElementLayout, count: int | None) -> None:
        """Lay out `element` (times `count` for an array) at the current offset."""
        self.entries.append((name, self.offset, element.width))
        for sub_path, sub_offset, sub_width in element.sub_entries:
            self.entries.append((f"{name}.{sub_path}", self.offset + sub_offset, sub_width))
        for sub_path, sub_size in element.sub_array_sizes.items():
            self.array_sizes[f"{name}.{sub_path}"] = sub_size
        total = element.width * (count or 1)
        if count is not None:
            self.array_sizes[name] = total
        self.offset += total


@dataclass
class _ElementLayout:
    """Width + flattened sub-layout of one field element (primitive or struct)."""

    width: int
    sub_entries: list[tuple[str, int, int]]
    sub_array_sizes: dict[str, int]


def split_array_type(field_type: str) -> tuple[str, int | None]:
    """Split `T[N]` into `(T, N)`; a scalar type yields `(T, None)`."""
    match = _ARRAY_TYPE_RE.fullmatch(field_type)
    if match is None:
        return field_type, None
    return match.group(1), eval_number(match.group(2))


def _element_layout(
    node: StructAstNode, field_name: str, element_type: str, resolver: Resolver, file_info: Token
) -> _ElementLayout:
    """Resolve a field's element type to its width and nested layout."""
    primitive_size = _STRUCT_FIELD_SIZES.get(element_type)
    if primitive_size is not None:
        return _ElementLayout(primitive_size, [], {})
    if element_type == node.name:
        raise NodeError(
            f"Struct {node.name!r} field {field_name!r} cannot reference its own type.",
            file_info,
        )
    if element_type not in resolver.struct_layouts:
        raise NodeError(
            f"Unknown struct field type {element_type!r} for {node.name}.{field_name}; "
            f"declare `.struct {element_type}` before use.",
            file_info,
        )
    return _ElementLayout(
        resolver.struct_sizes[element_type],
        resolver.struct_layouts[element_type],
        resolver.struct_array_sizes.get(element_type, {}),
    )


def _layout_struct_fields(node: StructAstNode, resolver: Resolver, file_info: Token) -> _StructLayout:
    """Compute the flat field layout + total size for a struct.

    Primitive fields contribute one entry whose ``width`` is the declared
    type's byte size (1/2/3/4). Nested struct fields contribute the parent
    field at its own offset with width equal to the nested struct's
    ``__size`` plus every flattened sub-entry inheriting its declared
    primitive width. An array field `T[N]` lays out like one `T` (its
    sub-entries describe element 0) but occupies `N` elements. Forward
    refs and self-references raise a NodeError.

    Width is what `lda p.field` needs to pick the right operand encoding
    later; without it auto-sizing would have to fall back to the string
    heuristic that already misfires for typed accesses.
    """
    layout = _StructLayout()
    for field_name, field_type in node.fields:
        bit_width = _bit_width_from_type(field_type)
        if bit_width is not None:
            layout.add_bit_field(field_name, bit_width)
            continue
        layout.flush_bits()
        element_type, count = split_array_type(field_type)
        element = _element_layout(node, field_name, element_type, resolver, file_info)
        layout.add_field(field_name, element, count)
    layout.flush_bits()
    return layout


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
    layout = _layout_struct_fields(node, resolver, file_info)
    entries, total_size, bit_meta = layout.entries, layout.offset, layout.bit_meta
    existing = resolver.struct_layouts.get(node.name)
    if existing is not None:
        if (
            existing == entries
            and resolver.struct_sizes.get(node.name) == total_size
            and resolver.struct_bitfields.get(node.name, {}) == bit_meta
            and resolver.struct_array_sizes.get(node.name, {}) == layout.array_sizes
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
    if layout.array_sizes:
        resolver.struct_array_sizes[node.name] = layout.array_sizes

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
    for field_path, array_size in layout.array_sizes.items():
        resolver.current_scope.add_symbol(f"{field_path}.__size", array_size)
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
    mapping = BusMapping(
        identifier=str(attributes["identifier"]),
        bank_range=attributes["bank_range"],
        addr_range=attributes["addr_range"],
        mask=attributes["mask"],
        writeable=attributes.get("writable", False),
        mirror_bank_range=attributes.get("mirror_bank_range"),
    )
    if _is_redeclaration(resolver.bus, mapping, file_info):
        return []
    _map_on_bus(resolver.bus, mapping)
    # OBJECT mode: serialize so the linker replays the mapping on its
    # own resolver bus. Without this, custom cartridge mappings
    # (SA-1, ExHiROM, anything beyond the default low_rom) silently
    # vanish at link time and downstream addresses resolve wrong.
    writer = resolver.context.object_writer
    if writer is not None and resolver.context.mode == AssemblyMode.OBJECT:
        writer.bus_mappings.append(mapping)
    return []


def _bus_shape(bus: Bus, identifier: str) -> tuple[object, ...] | None:
    """The declared shape of ``identifier`` on ``bus``, comparable with `_mapping_shape`."""
    declared = bus.mappings.get(identifier)
    if declared is None:
        return None
    mirror = bus.mappings.get(f"{identifier}_mirror")
    mirror_range = mirror.bank_range if mirror is not None else None
    return (declared.bank_range, declared.address_range, declared.mask, declared.writable, mirror_range)


def _mapping_shape(mapping: BusMapping) -> tuple[object, ...]:
    return (mapping.bank_range, mapping.addr_range, mapping.mask, mapping.writeable, mapping.mirror_bank_range)


def declare_bus_mapping(resolver: Resolver, mapping: BusMapping, file_info: Token) -> None:
    """Apply a `.map` declaration to the resolver bus.

    A `.map` reaches the bus once per declaring module, and `.import`
    brings the imported module's declarations along. Re-declaring an
    identifier identically is a no-op; a different shape under the same
    identifier is a conflict, matching the linker's cross-module check.
    """
    if not _is_redeclaration(resolver.bus, mapping, file_info):
        _map_on_bus(resolver.bus, mapping)


def _is_redeclaration(bus: Bus, mapping: BusMapping, file_info: Token) -> bool:
    """True when ``mapping`` is already declared identically; raise on a conflicting shape."""
    existing = _bus_shape(bus, mapping.identifier)
    if existing is None:
        return False
    if existing != _mapping_shape(mapping):
        raise NodeError(
            f"conflicting `.map {mapping.identifier!r}` declaration",
            file_info,
            code=str(E_CODEGEN_MAP_CONFLICT),
            hint="every module declaring this identifier must use the same bank_range, "
            "addr_range, mask, writable and mirror_bank_range",
        )
    return True


def _map_on_bus(bus: Bus, mapping: BusMapping) -> None:
    bus.map(
        mapping.identifier,
        mapping.bank_range,
        mapping.addr_range,
        mapping.mask,
        writeable=mapping.writeable,
        mirror_bank_range=mapping.mirror_bank_range,
    )


generators["struct"] = generate_struct
generators["map"] = generate_map
