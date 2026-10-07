"""`sizeof(...)` and `countof(...)`: sizes of structs, struct fields and reservations, and array element counts."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from a816.exceptions import LinkAssertError
from a816.formatter import A816Formatter
from a816.module_builder import BuildResult, build_with_imports
from a816.parse.nodes import NodeError
from a816.program import Program
from tests import StubWriter
from tests.test_reserve import _PREAMBLE, _link

_STRUCTS = """
ROWS = 3
.struct Pt {
    word x
    word y
}
.struct Path {
    byte count
    Pt[ROWS] points
    word[4] flags
    Pt origin
    u3 kind
    u5 rest
}
"""


def _word(expr: str, prelude: str = _STRUCTS) -> int:
    writer = StubWriter()
    Program().assemble_string_with_emitter(prelude + f"*=0x8000\n    .dw {expr}\n", "m.s", writer)
    return int.from_bytes(b"".join(writer.data), "little")


@pytest.mark.parametrize(
    ("expr", "value"),
    [
        ("sizeof(Pt)", 4),
        ("sizeof(Path)", 1 + 12 + 8 + 4 + 1),
        ("sizeof(Path.count)", 1),
        ("sizeof(Path.points)", 12),
        ("sizeof(Path.points.y)", 2),
        ("sizeof(Path.origin)", 4),
        ("countof(Path.points)", 3),
        ("countof(Path.flags)", 4),
        ("sizeof(Path.flags) / countof(Path.flags)", 2),
        ("(sizeof(Pt) + 1) * 2", 10),
    ],
)
def test_sizeof_and_countof_values(expr: str, value: int) -> None:
    assert _word(expr) == value


def _error(expr: str) -> NodeError:
    with pytest.raises(NodeError) as exc_info:
        _word(expr)
    return exc_info.value


@pytest.mark.parametrize(
    ("expr", "message", "hint"),
    [
        ("sizeof(Pth)", "no struct, reservation or alloc `Pth` is visible here", "did you mean `Path`?"),
        ("sizeof(Nope.x)", "no struct `Nope` is visible here", "`.import` the module that does"),
        ("countof(Nope.x)", "no struct `Nope` is visible here", "`.import` the module that does"),
        ("sizeof(Path.cnt)", "struct `Path` has no field `cnt`", "did you mean `Path.count`?"),
        ("sizeof(Path.kind)", "`kind` is a bit field and has no byte size", "`Path.kind.mask`"),
        ("countof(Path.count)", "`count` is not an array field", "countof counts the elements"),
        ("countof(Path)", "needs an array field", "countof counts the elements"),
    ],
)
def test_bad_arguments_report_e0321_at_the_argument(expr: str, message: str, hint: str) -> None:
    error = _error(expr)
    assert (error.code, message in str(error), hint in (error.hint or "")) == ("E0321", True, True)


def test_a_label_named_sizeof_is_still_a_label() -> None:
    assert _word("sizeof + 1", "sizeof = 0x41\n") == 0x42


def test_sizeof_sizes_a_struct_array_length() -> None:
    assert _word("sizeof(Buf)", _STRUCTS + ".struct Buf {\n    byte[sizeof(Pt) * 2] raw\n}\n") == 8


def test_countof_bounds_a_for_loop() -> None:
    src = _STRUCTS + "*=0x8000\n.for i := 0, countof(Path.points) {\n    nop\n}\n"
    writer = StubWriter()
    Program().assemble_string_with_emitter(src, "m.s", writer)
    assert b"".join(writer.data) == b"\xea" * 3


def test_the_formatter_keeps_the_operators() -> None:
    src = ".alloc user in code {\n    lda.w #sizeof(Path.points) + countof(Path.flags)\n}\n"
    assert A816Formatter().format_text(src) == src


def _object_code(src: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        return b"".join(section.code for section in _link(src, tmp).sections)


@pytest.mark.parametrize(
    ("reserve", "code"),
    [
        (".reserve buf 0x40 in wram\n", b"\xa2\x40\x00"),
        (".reserve buf SIZE in wram\nSIZE = 0x20\n", b"\xa2\x20\x00"),
        (".struct Pt {\n    word x\n    word y\n}\n.reserve buf as Pt in wram\n", b"\xa2\x04\x00"),
    ],
    ids=["constant size", "size defined later", "typed"],
)
def test_sizeof_a_reservation(reserve: str, code: bytes) -> None:
    assert _object_code(reserve + ".alloc user in code {\n    ldx.w #sizeof(buf)\n}\n") == code


@pytest.mark.parametrize(
    "alloc",
    [".alloc blob in code {\n    .db 1, 2, 3\n}\n", ".alloc blob at 0xc1e000 {\n    .db 1, 2, 3\n}\n"],
    ids=["pooled", "pinned"],
)
def test_sizeof_an_alloc(alloc: str) -> None:
    assert _object_code(alloc + ".alloc user in code {\n    ldx.w #sizeof(blob)\n}\n").endswith(b"\xa2\x03\x00")


def test_sizeof_inside_an_assert_is_folded_before_the_link() -> None:
    src = '.struct Pt {\n    word x\n    word y\n}\n.assert sizeof(Pt) == 4, "Pt is 4 bytes"\n'
    assert _object_code(src + ".alloc user in code {\n    nop\n}\n") == b"\xea"


def test_an_assert_the_linker_cannot_evaluate_keeps_its_message_and_line() -> None:
    src = '.assert 1 / 0 == 0, "never zero"\n.alloc user in code {\n    nop\n}\n'
    with pytest.raises(LinkAssertError) as exc_info:
        _object_code(src)
    message, _expression, source = exc_info.value.failures[0]
    assert (message.split(" (")[0], source.rsplit("/", 1)[-1]) == ("never zero", "m.s:6")


def test_a_failing_assert_with_sizeof_names_its_message() -> None:
    src = '.struct Pt {\n    word x\n}\n.assert sizeof(Pt) == 4, "Pt is 4 bytes"\n.alloc user in code {\n    nop\n}\n'
    with pytest.raises(LinkAssertError, match="Pt is 4 bytes"):
        _object_code(src)


def _build(root: Path, files: dict[str, str]) -> BuildResult:
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    return build_with_imports(root / "main.s", root / "out.ips", module_paths=[root], output_dir=root / "obj")


def _ips_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    out, i = b"", 5
    while data[i : i + 3] != b"EOF":
        size = int.from_bytes(data[i + 3 : i + 5], "big")
        out += data[i + 5 : i + 5 + size]
        i += 5 + size
    return out


@pytest.mark.parametrize(
    ("reserve", "code"),
    [
        (".reserve buf 0x40 in wram\n", b"\xa2\x40\x00"),
        (".struct Pt {\n    word x\n    word y\n}\n.reserve buf as Pt in wram\n", b"\xa2\x04\x00"),
    ],
    ids=["flat, resolved at link", "typed, known at import"],
)
def test_sizeof_an_imported_reservation(tmp_path: Path, reserve: str, code: bytes) -> None:
    main = '.import "lib"\n.alloc user at 0xc1f000 {\n    ldx.w #sizeof(buf)\n}\n'
    result = _build(tmp_path, {"lib.s": _PREAMBLE + reserve, "main.s": main})
    assert result.exit_code == 0, result.diagnostics
    assert _ips_bytes(tmp_path / "out.ips") == code


def test_sizeof_an_imported_alloc_resolves_at_link(tmp_path: Path) -> None:
    lib = _PREAMBLE + ".alloc blob in code {\n    .db 1, 2, 3, 4, 5\n}\n"
    main = '.import "lib"\n.alloc user at 0xc1f000 {\n    ldx.w #sizeof(blob)\n}\n'
    result = _build(tmp_path, {"lib.s": lib, "main.s": main})
    assert result.exit_code == 0, result.diagnostics
    assert _ips_bytes(tmp_path / "out.ips").endswith(b"\xa2\x05\x00")


def test_sizeof_folds_into_a_link_time_expression(tmp_path: Path) -> None:
    lib = _PREAMBLE + ".alloc table in code {\n    .db 0\n}\n"
    main = (
        '.import "lib"\n.struct Pt {\n    word x\n    word y\n}\n'
        ".alloc user at 0xc1f000 {\n    lda.l table + sizeof(Pt)\n}\n"
    )
    result = _build(tmp_path, {"lib.s": lib, "main.s": main})
    assert result.exit_code == 0, result.diagnostics
    table = result.symbol_map["table"] + 4
    assert _ips_bytes(tmp_path / "out.ips")[-4:] == bytes([0xAF, table & 0xFF, table >> 8 & 0xFF, table >> 16])


def test_the_language_server_colours_the_operator_as_a_keyword() -> None:
    from a816.lsp.server import A816Document, A816LanguageServer

    content = ".struct Pt {\n    word x\n}\nSIZE = sizeof(Pt)\n"
    tokens = A816LanguageServer()._extract_semantic_tokens_from_ast(A816Document("file:///s.s", content))
    assert {"line": 3, "char": 7, "length": 6, "type": 0} in tokens


def test_a_failing_assert_shows_an_alloc_size_as_its_value() -> None:
    src = '.alloc blob in code {\n    .db 1, 2, 3\n}\n.alloc user in code {\n    nop\n}\n.assert sizeof(blob) == 1, "probe"\n'
    with pytest.raises(LinkAssertError) as exc_info:
        _object_code(src)
    assert exc_info.value.failures[0][1] == "0x3 == 1"


def test_an_assert_without_a_message_says_so() -> None:
    from a816.parse.mzparser import A816Parser

    error = A816Parser.parse_as_ast(".assert 1 == 1\n", "m.s").error or ""
    assert "`.assert` needs a message after its condition" in error
