"""Every user-facing diagnostic carries a code and a caret under the offending token."""

from __future__ import annotations

import re
from dataclasses import dataclass

import pytest

from a816.parse.nodes import NodeError
from a816.program import Program
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
    with pytest.raises(NodeError) as exc_info:
        Program().assemble_string_with_emitter(src, "t.s", StubWriter())
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
