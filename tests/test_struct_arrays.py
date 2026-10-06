"""`TYPE[N] name` struct array fields: layout, size symbols, binds, tooling."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from a816.formatter import A816Formatter
from a816.linker import Linker
from a816.object_file import ObjectFile
from a816.parse.mzparser import A816Parser
from a816.parse.nodes import NodeError
from a816.program import Program
from tests import StubWriter

_HEADER = """
.struct Hdr {
    byte tag
    byte[21] title
    word version
}
"""

_POINTS = """
.struct Pt {
    word x
    word y
}
.struct Path {
    byte count
    Pt[3] points
    byte flags
}
.struct Outer {
    byte pad
    Path path
}
"""


@pytest.fixture(autouse=True)
def _disable_colors(monkeypatch: pytest.MonkeyPatch) -> None:
    import a816.errors as err_mod

    monkeypatch.setattr(err_mod, "_USE_COLORS", False)


def _symbols(src: str) -> dict[str, int]:
    program = Program()
    program.assemble_string_with_emitter(src, "arrays.s", StubWriter())
    return {
        name: value
        for scope in program.resolver.scopes
        for name, value in scope.symbols.items()
        if isinstance(value, int)
    }


def _parse_error(src: str) -> str:
    return A816Parser.parse_as_ast(src, "arrays.s").error or ""


def _codegen_error(src: str) -> NodeError:
    program = Program()
    writer = StubWriter()
    with pytest.raises(NodeError) as exc_info:
        program.assemble_string_with_emitter(src, "arrays.s", writer)
    return exc_info.value


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Hdr.tag", 0),
        ("Hdr.title", 1),
        ("Hdr.title.__size", 21),
        ("Hdr.version", 22),
        ("Hdr.__size", 24),
    ],
)
def test_byte_array_layout(name: str, expected: int) -> None:
    assert _symbols(_HEADER)[name] == expected


@pytest.mark.parametrize(
    ("field_type", "size"),
    [("byte", 4), ("word", 8), ("long", 12), ("dword", 16)],
)
def test_primitive_array_sizes(field_type: str, size: int) -> None:
    src = f".struct A {{\n    {field_type}[4] v\n    byte end\n}}\n"
    assert _symbols(src)["A.end"] == size


def test_hex_count_is_accepted() -> None:
    src = ".struct A {\n    byte[0x10] v\n}\n"
    assert _symbols(src)["A.__size"] == 16


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Path.points", 1),
        ("Path.points.x", 1),
        ("Path.points.y", 3),
        ("Path.points.__size", 12),
        ("Path.flags", 13),
        ("Path.__size", 14),
        ("Outer.path.points", 2),
        ("Outer.path.points.__size", 12),
    ],
)
def test_struct_array_layout(name: str, expected: int) -> None:
    assert _symbols(_POINTS)[name] == expected


def test_scalar_fields_get_no_size_symbol() -> None:
    assert "Hdr.version.__size" not in _symbols(_HEADER)


def test_identical_redefinition_with_arrays_is_noop() -> None:
    assert _symbols(_HEADER + _HEADER)["Hdr.__size"] == 24


def test_array_of_unknown_struct_errors() -> None:
    error = _codegen_error(".struct A {\n    Nope[2] v\n}\n")
    assert "Unknown struct field type 'Nope'" in str(error)


def test_zero_count_reports_e0120() -> None:
    assert "[E0120]" in _parse_error(".struct A {\n    byte[0] v\n}\n")


def test_zero_count_caret_under_count() -> None:
    assert "  |          ^\n" in _parse_error(".struct A {\n    byte[0] v\n}\n")


def test_bit_field_array_reports_e0121() -> None:
    assert "[E0121]" in _parse_error(".struct A {\n    u4[2] v\n}\n")


_PANEL = """
LINE_CELLS = 30
COPY_CELLS = 12
TILE_BYTES = 16
.struct PanelVwf {
    byte[(LINE_CELLS + COPY_CELLS) * TILE_BYTES] line_strip
    byte[LINE_CELLS] line_chars
}
"""


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("PanelVwf.line_strip.__size", 672),
        ("PanelVwf.line_chars", 672),
        ("PanelVwf.__size", 702),
    ],
)
def test_count_takes_a_constant_expression(name: str, expected: int) -> None:
    assert _symbols(_PANEL)[name] == expected


def test_istruct_fills_an_expression_sized_array() -> None:
    src = "N = 2\n.struct A {\n    word[N + 1] v\n}\n.istruct A { v = [1, 2, 3] }\n"
    writer = StubWriter()
    Program().assemble_string_with_emitter(src, "arrays.s", writer)
    assert b"".join(writer.data) == bytes([1, 0, 2, 0, 3, 0])


def test_formatter_keeps_an_expression_count() -> None:
    src = ".struct A {\n    byte[N * 2] v\n}\n"
    assert A816Formatter().format_text(src) == src


def test_undefined_constant_count_reports_e0120() -> None:
    assert "E0120" in str(_codegen_error(".struct A {\n    byte[N] v\n}\n"))


def test_non_positive_expression_count_reports_e0120() -> None:
    assert "E0120" in str(_codegen_error("N = 1\n.struct A {\n    byte[N - 1] v\n}\n"))


def test_unclosed_count_is_rejected() -> None:
    assert "[E0101]" in _parse_error(".struct A {\n    byte[2 v\n}\n")


def test_canonical_keeps_array_type() -> None:
    node = A816Parser.parse_as_ast(".struct A {\n    byte[0x15] title\n}\n", "a.s").nodes[0]
    assert node.to_canonical() == ".struct A {\n    byte[0x15] title\n}"


def test_formatter_round_trips_array_field() -> None:
    src = ".struct A {\n    byte[21] title\n    Pt[2] pts\n}\n"
    assert A816Formatter().format_text(src) == src


def test_typed_bind_offsets_array_fields() -> None:
    symbols = _symbols(_HEADER + "h := (0x7E0000 as Hdr)\n")
    assert symbols["h.version"] == 0x7E0016


def test_inline_cast_reaches_struct_array_element_zero() -> None:
    writer = StubWriter()
    src = _POINTS + "*=0x8000\nlda.w (0x1000 as Path).points.y\n"
    Program().assemble_string_with_emitter(src, "arrays.s", writer)
    assert b"".join(writer.data) == b"\xad\x03\x10"


def test_typed_reserve_spans_whole_array() -> None:
    preamble = (
        ".map identifier=3 bank_range=0x7e, 0x7f addr_range=0x0000, 0xffff mask=0x10000 writable=1\n"
        ".pool wram { bss  range 0x7e0000 0x7e1fff  strategy order }\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        asm = Path(tmp) / "m.s"
        asm.write_text(preamble + _HEADER + ".reserve hdr as Hdr in wram\n.reserve after 0x1 in wram\n")
        obj = Path(tmp) / "m.o"
        assert Program().assemble_as_object(str(asm), obj) == 0
        linked = Linker([ObjectFile.from_file(str(obj))]).link(base_address=0x8000)
    symbols = {name: value for name, value, *_ in linked.symbols}
    assert symbols["after"] == 0x7E0018
