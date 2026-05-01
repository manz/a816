"""Codegen for `.struct`: emits dotted offset constants and __size."""

from __future__ import annotations

import tempfile
from pathlib import Path

from a816.program import Program


def _resolve(source: str) -> Program:
    """Run a program through resolve so resolver scopes are populated."""
    program = Program()
    error = program.assemble_string_with_emitter(source, "memory.s", _NoopEmitter())
    assert error is None, error
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
    program = Program()
    src = """
        .struct Bad {
            qword x
        }
    """
    error, _ = program.parser.parse(src, "memory.s")
    assert error is not None
    assert "Unknown struct field type" in str(error)


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
