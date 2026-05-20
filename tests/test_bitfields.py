"""`.struct` bit-field declarations using `uN` type prefixes.

Each bit field publishes three symbols:
  - `Type.field`        — byte offset of the containing byte.
  - `Type.field.mask`   — pre-shifted mask, ready as an immediate operand.
  - `Type.field.shift`  — LSB position inside the byte.
"""

from __future__ import annotations

import pytest

from a816.parse.nodes import NodeError
from a816.program import Program


def _resolve(source: str) -> Program:
    program = Program()
    program.assemble_string_with_emitter(source, "memory.s", _NoopEmitter())
    return program


def _symbols(program: Program) -> dict[str, int]:
    return {
        name: value
        for scope in program.resolver.scopes
        for name, value in scope.symbols.items()
        if isinstance(value, int)
    }


def test_single_byte_bitfield_layout() -> None:
    program = _resolve("""
        .struct INIDISP {
            u4 brightness
            u3 unused
            u1 force_blank
        }
    """)
    s = _symbols(program)
    assert s["INIDISP.__size"] == 1
    # Field "address" symbol = byte offset (always 0 for a 1-byte struct).
    assert s["INIDISP.brightness"] == 0
    assert s["INIDISP.unused"] == 0
    assert s["INIDISP.force_blank"] == 0
    # Pre-shifted masks suitable for AND/ORA immediates.
    assert s["INIDISP.brightness.mask"] == 0x0F
    assert s["INIDISP.unused.mask"] == 0x70
    assert s["INIDISP.force_blank.mask"] == 0x80
    # LSB positions.
    assert s["INIDISP.brightness.shift"] == 0
    assert s["INIDISP.unused.shift"] == 4
    assert s["INIDISP.force_blank.shift"] == 7


def test_multi_byte_bitfield_packs_across_bytes() -> None:
    program = _resolve("""
        .struct Sixteen {
            u4 low_nibble
            u8 mid_byte
            u4 high_nibble
        }
    """)
    s = _symbols(program)
    assert s["Sixteen.__size"] == 2
    assert s["Sixteen.low_nibble"] == 0
    assert s["Sixteen.low_nibble.mask"] == 0x0F
    assert s["Sixteen.low_nibble.shift"] == 0
    # mid_byte starts at bit 4 of byte 0
    assert s["Sixteen.mid_byte"] == 0
    assert s["Sixteen.mid_byte.shift"] == 4
    # high_nibble starts at bit 12 → byte 1, shift 4
    assert s["Sixteen.high_nibble"] == 1
    assert s["Sixteen.high_nibble.shift"] == 4
    assert s["Sixteen.high_nibble.mask"] == 0xF0


def test_mixed_bit_and_primitive_fields() -> None:
    program = _resolve("""
        .struct Mixed {
            u1 flag_a
            u1 flag_b
            u6 unused
            byte tag
            word value
        }
    """)
    s = _symbols(program)
    assert s["Mixed.flag_a"] == 0
    assert s["Mixed.flag_a.mask"] == 0x01
    assert s["Mixed.flag_b"] == 0
    assert s["Mixed.flag_b.mask"] == 0x02
    assert s["Mixed.tag"] == 1
    assert s["Mixed.value"] == 2
    assert s["Mixed.__size"] == 4


def test_typed_bind_resolves_bitfield_byte_address() -> None:
    """A typed bind on a bitfield struct exposes per-field byte addresses."""
    program = _resolve("""
        .struct INIDISP {
            u4 brightness
            u3 unused
            u1 force_blank
        }
        ppu_inidisp := (0x2100 as INIDISP)
    """)
    s = _symbols(program)
    assert s["ppu_inidisp.force_blank"] == 0x2100
    # The mask/shift symbols are absolute constants, owned by the type's
    # scope — they're NOT shifted by the instance base.
    assert s["INIDISP.force_blank.mask"] == 0x80


def test_u8_packs_a_full_byte() -> None:
    """`u8 foo` is a valid bit-field type — it just fills the byte."""
    program = _resolve("""
        .struct Whole {
            u8 value
        }
    """)
    s = _symbols(program)
    assert s["Whole.value"] == 0
    assert s["Whole.value.mask"] == 0xFF
    assert s["Whole.value.shift"] == 0
    assert s["Whole.__size"] == 1


def test_idempotent_redef_of_bitfield_struct() -> None:
    """Identical re-declaration is a no-op (covers double-include cases)."""
    program = _resolve("""
        .struct INIDISP {
            u4 brightness
            u3 unused
            u1 force_blank
        }
        .struct INIDISP {
            u4 brightness
            u3 unused
            u1 force_blank
        }
    """)
    s = _symbols(program)
    assert s["INIDISP.force_blank.mask"] == 0x80


def test_mismatched_bitfield_redef_raises() -> None:
    program = Program()
    src = """
        .struct INIDISP {
            u4 brightness
            u3 unused
            u1 force_blank
        }
        .struct INIDISP {
            u3 brightness
            u4 unused
            u1 force_blank
        }
    """
    with pytest.raises(NodeError, match="different field layout"):
        program.assemble_string_with_emitter(src, "memory.s", _NoopEmitter())


class _NoopEmitter:
    def begin(self) -> None: ...

    def end(self) -> None: ...

    def write_block_header(self, *_: object, **__: object) -> None: ...

    def write_block(self, *_: object, **__: object) -> None: ...
