"""Codegen for `.struct`: emits dotted offset constants and __size."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from a816.parse.nodes import NodeError
from a816.program import Program


def _resolve(source: str) -> Program:
    """Run a program through resolve so resolver scopes are populated."""
    program = Program()
    program.assemble_string_with_emitter(source, "memory.s", _NoopEmitter())
    return program


class _NoopEmitter:
    def begin(self) -> None: ...

    def end(self) -> None: ...

    def write_block_header(self, *_: object, **__: object) -> None: ...

    def write_block(self, *_: object, **__: object) -> None: ...


def test_struct_emits_field_offsets_and_size() -> None:
    program = _resolve(
        """
        .struct OAM {
            word x
            byte y
            byte tile
            byte attr
        }
        """
    )

    labels = dict(program.resolver.get_all_labels())
    # Symbols, not labels — fields are constants.
    symbols: dict[str, int] = {}
    for scope in program.resolver.scopes:
        for name, value in scope.symbols.items():
            if isinstance(value, int):
                symbols[name] = value

    assert symbols["OAM.x"] == 0
    assert symbols["OAM.y"] == 2
    assert symbols["OAM.tile"] == 3
    assert symbols["OAM.attr"] == 4
    assert symbols["OAM.__size"] == 5
    # Struct fields are not labels.
    assert "OAM.x" not in labels


def test_struct_offsets_resolve_in_expressions() -> None:
    """`lda.b ptr + Struct.field` assembles with the right operand."""

    src = """
        .struct PPU {
            byte INIDISP
            byte OBSEL
            word OAMADDR
        }
        *=0x008000
            lda.w 0x2100 + PPU.OAMADDR
    """

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        asm = root / "main.s"
        asm.write_text(src, encoding="utf-8")
        ips = root / "out.ips"
        program = Program()
        assert program.assemble_as_patch(str(asm), ips) == 0
        assert ips.exists()
        # IPS contents must contain `AD 02 21` (LDA $2102) — operand = 0x2100 + 2.
        data = ips.read_bytes()
        assert b"\xad\x02\x21" in data


def test_struct_duplicate_field_raises() -> None:
    program = Program()
    src = """
        .struct Bad {
            byte x
            byte x
        }
    """
    error, _ = program.parser.parse(src, "memory.s")
    assert error is not None
    assert "Duplicate struct field" in str(error)


def test_struct_unknown_type_raises() -> None:
    """Unknown type is now validated at codegen so nested struct types compose."""
    program = Program()
    src = """
        .struct Bad {
            qword x
        }
    """
    with pytest.raises(NodeError, match="Unknown struct field type"):
        program.assemble_string_with_emitter(src, "memory.s", _NoopEmitter())


def test_struct_supports_dword() -> None:
    program = _resolve(
        """
        .struct Big {
            byte tag
            dword payload
        }
        """
    )
    symbols: dict[str, int] = {
        name: value
        for scope in program.resolver.scopes
        for name, value in scope.symbols.items()
        if isinstance(value, int)
    }
    assert symbols["Big.tag"] == 0
    assert symbols["Big.payload"] == 1
    assert symbols["Big.__size"] == 5


def _collect_symbols(program: Program) -> dict[str, int]:
    return {
        name: value
        for scope in program.resolver.scopes
        for name, value in scope.symbols.items()
        if isinstance(value, int)
    }


def _assemble_ips(src: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        asm = root / "main.s"
        asm.write_text(src, encoding="utf-8")
        ips = root / "out.ips"
        program = Program()
        assert program.assemble_as_patch(str(asm), ips) == 0
        return ips.read_bytes()


_OAM_DEF = """
.struct OAM {
    word x
    word y
    byte tile
    byte attr
}
"""

_PT_DEF = """
.struct Pt {
    word x
    word y
}
"""

_NESTED_DEFS = """
.struct Inner {
    word x
    word y
}
.struct Outer {
    byte tag
    Inner pos
    byte flags
}
"""


def test_inline_cast_field_access() -> None:
    """`lda.w (0x2100 as PPU).OAMADDR` assembles to LDA $2102."""
    src = """
        .struct PPU {
            byte INIDISP
            byte OBSEL
            word OAMADDR
        }
        *=0x008000
            lda.w (0x2100 as PPU).OAMADDR
    """
    data = _assemble_ips(src)
    assert b"\xad\x02\x21" in data


def test_typed_bind_paren_form_registers_field_symbols() -> None:
    program = _resolve(_OAM_DEF + "p := (0x7e0000 as OAM)\n")
    symbols = _collect_symbols(program)
    assert symbols["p"] == 0x7E0000
    assert symbols["p.x"] == 0x7E0000
    assert symbols["p.y"] == 0x7E0002
    assert symbols["p.tile"] == 0x7E0004
    assert symbols["p.attr"] == 0x7E0005
    assert program.resolver.typed_instances["p"] == "OAM"


def test_typed_bind_bare_form() -> None:
    program = _resolve(_PT_DEF + "q := 0x010000 as Pt\n")
    symbols = _collect_symbols(program)
    assert symbols["q"] == 0x010000
    assert symbols["q.x"] == 0x010000
    assert symbols["q.y"] == 0x010002


def test_typed_bind_dot_access_in_opcode() -> None:
    src = (
        _OAM_DEF
        + """
        *=0x008000
        p := (0x7e0000 as OAM)
            lda.l p.y
    """
    )
    data = _assemble_ips(src)
    # LDA.l $7E0002 = AF 02 00 7E
    assert b"\xaf\x02\x00\x7e" in data


def test_nested_struct_fields_register_dotted_subfields() -> None:
    program = _resolve(_NESTED_DEFS)
    symbols = _collect_symbols(program)
    assert symbols["Outer.tag"] == 0
    assert symbols["Outer.pos"] == 1
    assert symbols["Outer.pos.x"] == 1
    assert symbols["Outer.pos.y"] == 3
    assert symbols["Outer.flags"] == 5
    assert symbols["Outer.__size"] == 6


def test_nested_typed_bind_registers_deep_field_symbols() -> None:
    program = _resolve(_NESTED_DEFS + "o := (0x7e0010 as Outer)\n")
    symbols = _collect_symbols(program)
    assert symbols["o"] == 0x7E0010
    assert symbols["o.tag"] == 0x7E0010
    assert symbols["o.pos"] == 0x7E0011
    assert symbols["o.pos.x"] == 0x7E0011
    assert symbols["o.pos.y"] == 0x7E0013
    assert symbols["o.flags"] == 0x7E0015


def test_forward_ref_nested_type_raises() -> None:
    program = Program()
    src = """
        .struct Outer {
            Inner i
        }
        .struct Inner {
            word x
        }
    """
    with pytest.raises(NodeError, match="Unknown struct field type 'Inner'"):
        program.assemble_string_with_emitter(src, "memory.s", _NoopEmitter())


def test_self_reference_nested_type_raises() -> None:
    program = Program()
    src = """
        .struct Node {
            Node next
        }
    """
    with pytest.raises(NodeError, match="cannot reference its own type"):
        program.assemble_string_with_emitter(src, "memory.s", _NoopEmitter())


def test_cast_preserves_inner_expression() -> None:
    src = (
        _PT_DEF
        + """
        *=0x008000
            lda.w ((0x2100 + 4) as Pt).y
    """
    )
    data = _assemble_ips(src)
    # base = 0x2104, Pt.y = 2, operand = 0x2106 → AD 06 21
    assert b"\xad\x06\x21" in data


def test_cast_chained_field_access() -> None:
    src = (
        _NESTED_DEFS
        + """
        *=0x008000
            lda.l (0x7e0000 as Outer).pos.y
    """
    )
    data = _assemble_ips(src)
    # Outer.pos.y = 1 (pos offset) + 2 (Inner.y) = 3, base = 0x7E0000
    # LDA.l $7E0003 = AF 03 00 7E
    assert b"\xaf\x03\x00\x7e" in data


def test_typed_bind_with_equal_rejected() -> None:
    program = Program()
    src = _PT_DEF + "p = 0x100 as Pt\n"
    error, _ = program.parser.parse(src, "memory.s")
    assert error is not None
    assert "requires `:=`" in str(error)


def test_typed_bind_chain_through_intermediate_constant() -> None:
    """A typed bind can resolve `_p` defined by a prior `:=` constant."""
    program = _resolve(
        _PT_DEF
        + """
        _STRUCT_BASE_ADDR := 0x7e8000
        _p := _STRUCT_BASE_ADDR + 3 * Pt.__size
        p := (_p as Pt)
        """
    )
    symbols = _collect_symbols(program)
    assert symbols["_STRUCT_BASE_ADDR"] == 0x7E8000
    assert symbols["_p"] == 0x7E800C
    assert symbols["p"] == 0x7E800C
    assert symbols["p.x"] == 0x7E800C
    assert symbols["p.y"] == 0x7E800E


def test_typed_bind_with_inline_expression_inside_cast() -> None:
    """Inner cast expression can be any compile-time expression."""
    program = _resolve(
        _PT_DEF
        + """
        _STRUCT_BASE_ADDR := 0x7e8000
        p := ((_STRUCT_BASE_ADDR + 3 * Pt.__size) as Pt)
        """
    )
    symbols = _collect_symbols(program)
    assert symbols["p"] == 0x7E800C
    assert symbols["p.x"] == 0x7E800C
    assert symbols["p.y"] == 0x7E800E


def test_formatter_round_trip_for_cast_and_typed_bind() -> None:
    """Cast, typed bind, and dot-access survive a format-parse round trip."""
    from a816.formatter import A816Formatter

    src = (
        ".struct Pt {\n"
        "    word x\n"
        "    word y\n"
        "}\n"
        "\n"
        "*=0x008000\n"
        "p := (0x7e0000 as Pt)\n"
        "    lda.l p.y\n"
        "    lda.w (0x2100 as Pt).y\n"
    )
    fmt = A816Formatter()
    first = fmt.format_text(src)
    second = fmt.format_text(first + "\n")
    assert first == second
    assert "p := (0x7e0000 as Pt)" in first
    assert "(0x2100 as Pt).y" in first
    assert "p.y" in first


def test_field_access_without_cast_rejected() -> None:
    program = Program()
    src = """
        *=0x008000
            lda.w (0x2100).hp
    """
    error, _ = program.parser.parse(src, "memory.s")
    assert error is not None
    assert "Field access requires a typed cast" in str(error)
