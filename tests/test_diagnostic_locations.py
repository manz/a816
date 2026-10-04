"""Every user-facing diagnostic carries a code and a caret under the offending token."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from a816.exceptions import LinkerError
from a816.linker import Linker
from a816.object_file import ObjectFile, PoolAlloc
from a816.parse.nodes import NodeError
from a816.program import Program
from a816.writers import ObjectWriter
from tests import StubWriter

_CODE_RE = re.compile(r"error\[(E\d{4})\]")
_LOCATION_RE = re.compile(r"--> [^:]+:(\d+):(\d+)")


@dataclass(frozen=True)
class Rendered:
    """The parts of a rendered diagnostic a test asserts on."""

    code: str | None
    line: int | None
    underlined: str | None


@pytest.fixture(autouse=True)
def _disable_colors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    import a816.errors as err_mod

    monkeypatch.setattr(err_mod, "_USE_COLORS", False)


def _underlined(rendered: str) -> str | None:
    """Source text sitting under the `^^^` caret run of a rendered block."""
    lines = rendered.splitlines()
    for index, line in enumerate(lines):
        _, _, marker = line.partition("| ")
        if index and marker.strip() and set(marker.strip()) == {"^"}:
            source = lines[index - 1].partition("| ")[2]
            start = marker.index("^")
            return source[start : start + marker.count("^")]
    return None


def parse_rendered(rendered: str) -> Rendered:
    code = _CODE_RE.search(rendered)
    location = _LOCATION_RE.search(rendered)
    return Rendered(
        code=code.group(1) if code else None,
        line=int(location.group(1)) if location else None,
        underlined=_underlined(rendered),
    )


def _assemble_error(src: str) -> Rendered:
    program = Program()
    writer = StubWriter()
    with pytest.raises(NodeError) as exc_info:
        program.assemble_string_with_emitter(src, "t.s", writer)
    return parse_rendered(str(exc_info.value))


_STRUCT_S = ".struct S {\n    word x\n}\n"

SYMBOL_CASES = [
    pytest.param("    lda.w some_undefined\n", 1, "some_undefined", id="bare-operand"),
    pytest.param("base = 0x10\n    lda.l base + missing\n", 2, "missing", id="binop-rhs"),
    pytest.param(_STRUCT_S + "    lda.w (0x10 as S).zz\n", 4, "zz", id="cast-field"),
    pytest.param("    .dw 1, missing\n", 1, "missing", id="data-word"),
    pytest.param("    .db missing\n", 1, "missing", id="data-byte"),
    pytest.param(".pool p { range 0x8000 missing }\n", 1, "missing", id="pool-literal"),
    pytest.param("my_routine:\n    rts\n    jsr.l my_routime\n", 3, "my_routime", id="errors-md-example"),
]


@pytest.mark.parametrize(("src", "line", "token"), SYMBOL_CASES)
def test_undefined_symbol_code(src: str, line: int, token: str) -> None:
    assert _assemble_error(src).code == "E0200"


@pytest.mark.parametrize(("src", "line", "token"), SYMBOL_CASES)
def test_undefined_symbol_line(src: str, line: int, token: str) -> None:
    assert _assemble_error(src).line == line


@pytest.mark.parametrize(("src", "line", "token"), SYMBOL_CASES)
def test_undefined_symbol_caret(src: str, line: int, token: str) -> None:
    assert _assemble_error(src).underlined == token


_BSS_POOL = ".pool st { range 0x7e2000 0x7e2fff bss }\n"

PLACEMENT_CASES = [
    pytest.param(".alloc foo in NOPE {\n    nop\n}\n", "E0205", 1, "NOPE", id="alloc-unknown-pool"),
    pytest.param(".alloc in NOPE {\n    nop\n}\n", "E0205", 1, "NOPE", id="anon-alloc-unknown-pool"),
    pytest.param(".reserve thing 4 in nopool\n", "E0205", 1, "nopool", id="reserve-unknown-pool"),
    pytest.param(_STRUCT_S + ".reserve thing as S in nopool\n", "E0205", 4, "nopool", id="typed-reserve-unknown-pool"),
    pytest.param(_BSS_POOL + ".reserve thing as Nope in st\n", "E0206", 2, "Nope", id="typed-reserve-unknown-type"),
    pytest.param(
        ".relocate sym 0x8000 0x8010 into gone {\n    nop\n}\n", "E0205", 1, "gone", id="relocate-unknown-pool"
    ),
    pytest.param(".reclaim gone 0x8000 0x8010\n", "E0205", 1, "gone", id="reclaim-unknown-pool"),
]


@pytest.mark.parametrize(("src", "code", "line", "token"), PLACEMENT_CASES)
def test_placement_code(src: str, code: str, line: int, token: str) -> None:
    assert _assemble_error(src).code == code


@pytest.mark.parametrize(("src", "code", "line", "token"), PLACEMENT_CASES)
def test_placement_line(src: str, code: str, line: int, token: str) -> None:
    assert _assemble_error(src).line == line


@pytest.mark.parametrize(("src", "code", "line", "token"), PLACEMENT_CASES)
def test_placement_caret(src: str, code: str, line: int, token: str) -> None:
    assert _assemble_error(src).underlined == token


EMIT_CASES = [
    pytest.param(
        "*=0x8000\nstart:\n    bra far\n.db 0\n*=0x8200\nfar:\n    nop\n", "E0315", 3, "far", id="branch-out-of-range"
    ),
    pytest.param("*=0x8000\n    bra 0x7e0000\n", "E0316", 2, "0x7e0000", id="branch-into-ram"),
    pytest.param("    nomacro(1)\n", "E0207", 1, "nomacro", id="unknown-macro"),
    pytest.param(".macro m(a) {\n    nop\n}\n    m(1, 2)\n", "E0208", 4, "m", id="macro-arity"),
    pytest.param("*=0x708000\n    nop\n", "E0317", 1, "0x708000", id="unmapped-bank"),
    pytest.param('*=0x8000\n    lda.w #"a" + 1\n', "E0319", 2, "+", id="mismatched-types"),
    pytest.param("*=0x8000\n    lda.w #~0x100000000\n", "E0320", 2, "~", id="bitwise-not-too-wide"),
    pytest.param("*=0x8000\n.dw ~0x100000000\n", "E0320", 2, "~", id="bitwise-not-too-wide-data"),
    pytest.param(_STRUCT_S + '*=0x8000\n    lda.w ("x" as S).x\n', "E0305", 5, '"x"', id="cast-base-not-address"),
    pytest.param(
        ".macro m(b) {\n    lda.w b\n}\n*=0x8000\n    m({\n    nop\n})\n", "E0209", 2, "b", id="block-used-as-value"
    ),
]


@pytest.mark.parametrize(("src", "code", "line", "token"), EMIT_CASES)
def test_emit_code(src: str, code: str, line: int, token: str) -> None:
    assert _assemble_error(src).code == code


@pytest.mark.parametrize(("src", "code", "line", "token"), EMIT_CASES)
def test_emit_line(src: str, code: str, line: int, token: str) -> None:
    assert _assemble_error(src).line == line


@pytest.mark.parametrize(("src", "code", "line", "token"), EMIT_CASES)
def test_emit_caret(src: str, code: str, line: int, token: str) -> None:
    assert _assemble_error(src).underlined == token


def _include_ips_error(path: Path) -> Rendered:
    return _assemble_error(f'.include_ips "{path}", 0\n')


def test_include_ips_without_header_code(tmp_path: Path) -> None:
    patch = tmp_path / "bad.ips"
    patch.write_bytes(b"NOPE")
    assert _include_ips_error(patch).code == "E0502"


def test_include_ips_without_header_caret(tmp_path: Path) -> None:
    patch = tmp_path / "bad.ips"
    patch.write_bytes(b"NOPE")
    assert _include_ips_error(patch).underlined == f'"{patch}"'


def test_include_ips_missing_file_code(tmp_path: Path) -> None:
    assert _include_ips_error(tmp_path / "absent.ips").code == "E0500"


def test_include_ips_missing_file_caret(tmp_path: Path) -> None:
    patch = tmp_path / "absent.ips"
    assert _include_ips_error(patch).underlined == f'"{patch}"'


_OVERFLOW_SRC = ".pool p { range 0x008000 0x008003 }\n.alloc foo in p {\n    .db 1, 2, 3, 4, 5, 6\n}\n"


def test_pool_overflow_code() -> None:
    assert _assemble_error(_OVERFLOW_SRC).code == "E0318"


def test_pool_overflow_caret_on_alloc_name() -> None:
    assert _assemble_error(_OVERFLOW_SRC).underlined == "foo"


def test_pool_overflow_names_largest_free_chunk() -> None:
    program = Program()
    writer = StubWriter()
    with pytest.raises(NodeError) as exc_info:
        program.assemble_string_with_emitter(_OVERFLOW_SRC, "t.s", writer)
    assert "largest free chunk is 4 bytes" in str(exc_info.value)


def _link_error(tmp_path: Path, src: str) -> LinkerError:
    source = tmp_path / "main.s"
    source.write_text(src, encoding="utf-8")
    obj = tmp_path / "main.o"
    Program().assemble_as_object(str(source), obj)
    linker = Linker([ObjectFile.from_file(str(obj))])
    with pytest.raises(LinkerError) as exc_info:
        linker.link()
    return exc_info.value


def test_link_pool_overflow_code(tmp_path: Path) -> None:
    assert "[E0404]" in _link_error(tmp_path, _OVERFLOW_SRC).format()


def test_link_pool_overflow_names_pool(tmp_path: Path) -> None:
    assert "pool: p" in _link_error(tmp_path, _OVERFLOW_SRC).format()


def test_link_pool_overflow_names_largest_free_chunk(tmp_path: Path) -> None:
    assert "largest free chunk: 4 bytes" in _link_error(tmp_path, _OVERFLOW_SRC).format()


def test_link_pool_overflow_points_at_alloc_body(tmp_path: Path) -> None:
    src = ".pool p { range 0x008000 0x008003 }\n.alloc foo in p {\n    nop\n    .db 1, 2, 3, 4, 5, 6\n}\n"
    assert f"alloc body: {tmp_path / 'main.s'}:3" in _link_error(tmp_path, src).format()


def test_link_alloc_into_undeclared_pool_code() -> None:
    orphan = ObjectFile([], [], pool_allocs=[PoolAlloc(pool_name="gone", symbol_name="foo", section_idx=0, size=1)])
    linker = Linker([orphan])
    with pytest.raises(LinkerError) as exc_info:
        linker.link()
    assert "[E0405]" in exc_info.value.format()


def test_object_mode_unmapped_alloc_code(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    source = tmp_path / "main.s"
    source.write_text(".alloc main at 0x708000 {\n    nop\n}\n", encoding="utf-8")
    writer = ObjectWriter(str(tmp_path / "main.o"))
    writer.begin()
    with caplog.at_level(logging.ERROR):
        Program().assemble_with_object_emitter(str(source), writer)
    assert any("[E0317]" in record.getMessage() for record in caplog.records)


def _parse_cli(argv: list[str]) -> int | str | None:
    from a816.cli import _build_arg_parser

    parser = _build_arg_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(argv)
    return exc_info.value.code


def test_cli_rejects_unknown_mapping_exit_code() -> None:
    assert _parse_cli(["main.s", "-m", "high_rom"]) == 2


def test_cli_unknown_mapping_lists_choices(capsys: pytest.CaptureFixture[str]) -> None:
    _parse_cli(["main.s", "-m", "high_rom"])
    assert "invalid choice: 'high_rom'" in capsys.readouterr().err


def _object_mode_error(src: str, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> Rendered:
    """Compile `src` to a `.o` (the `a816 build` path) and parse the logged error."""
    source = tmp_path / "t.s"
    source.write_text(src, encoding="utf-8")
    writer = ObjectWriter(str(tmp_path / "t.o"))
    writer.begin()
    with caplog.at_level(logging.ERROR):
        Program().assemble_with_object_emitter(str(source), writer)
    messages = [record.getMessage() for record in caplog.records if record.levelno == logging.ERROR]
    return parse_rendered(messages[0] if messages else "")


@pytest.mark.parametrize(("src", "line", "token"), SYMBOL_CASES)
def test_object_mode_undefined_symbol_caret(
    src: str, line: int, token: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert _object_mode_error(src, tmp_path, caplog).underlined == token


@pytest.mark.parametrize(("src", "code", "line", "token"), PLACEMENT_CASES + EMIT_CASES)
def test_object_mode_code(
    src: str, code: str, line: int, token: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert _object_mode_error(src, tmp_path, caplog).code == code


@pytest.mark.parametrize(("src", "code", "line", "token"), PLACEMENT_CASES + EMIT_CASES)
def test_object_mode_caret(
    src: str, code: str, line: int, token: str, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    assert _object_mode_error(src, tmp_path, caplog).underlined == token
