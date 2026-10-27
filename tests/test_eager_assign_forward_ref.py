"""`:=` evaluates at codegen: forward refs get a located E0210 diagnostic."""

from __future__ import annotations

import logging
from itertools import pairwise

import pytest

from tests import link_source

PLAIN_SRC = """P := later
later = 0x1234
*=0x8000
lda.w #P
"""

TYPED_SRC = """.struct S {
    word x
}
P := (later as S)
later = 0x1234
*=0x8000
lda.w P.x
"""

NESTED_SRC = """P := 1 + (later + 2)
later = 0x1234
"""

CAST_THEN_REF_SRC = """.struct S {
    word x
}
P := (0 as S) + later
later = 0x1234
"""


@pytest.fixture(autouse=True)
def _disable_colors(monkeypatch: pytest.MonkeyPatch) -> None:
    import a816.errors as err_mod

    monkeypatch.setattr(err_mod, "_USE_COLORS", False)


def _assemble_error(src: str, caplog: pytest.LogCaptureFixture) -> str:
    """The rendered diagnostic of a build that fails on `src`."""
    with caplog.at_level(logging.ERROR, logger="a816"):
        linked = link_source(src)
    assert linked.exit_code != 0
    return caplog.text


def _pointed(rendered: str) -> str:
    """The source text the rendered diagnostic's caret underlines."""
    lines = rendered.splitlines()
    for source, caret in pairwise(lines):
        marks = caret.partition("| ")[2]
        if "|" in source and marks.strip() and set(marks.strip()) == {"^"}:
            start = marks.index("^")
            return source.partition("| ")[2][start : start + marks.count("^")]
    return ""


@pytest.mark.parametrize("src", [PLAIN_SRC, TYPED_SRC, NESTED_SRC, CAST_THEN_REF_SRC])
def test_forward_ref_carries_code(src: str, caplog: pytest.LogCaptureFixture) -> None:
    assert "error[E0210]" in _assemble_error(src, caplog)


@pytest.mark.parametrize("src", [PLAIN_SRC, TYPED_SRC, NESTED_SRC, CAST_THEN_REF_SRC])
def test_forward_ref_points_at_symbol_token(src: str, caplog: pytest.LogCaptureFixture) -> None:
    assert _pointed(_assemble_error(src, caplog)) == "later"


def test_plain_forward_ref_caret_column(caplog: pytest.LogCaptureFixture) -> None:
    assert f"main.s:1:{len('P := ') + 1}\n" in _assemble_error(PLAIN_SRC, caplog)


def test_typed_forward_ref_caret_line(caplog: pytest.LogCaptureFixture) -> None:
    assert "main.s:4:" in _assemble_error(TYPED_SRC, caplog)


def test_forward_ref_hint_suggests_lazy_assign(caplog: pytest.LogCaptureFixture) -> None:
    assert "hint: `:=` evaluates immediately; use `=` for a forward reference" in _assemble_error(PLAIN_SRC, caplog)


def test_forward_ref_message_names_symbol(caplog: pytest.LogCaptureFixture) -> None:
    assert "error[E0210]: `later`" in _assemble_error(PLAIN_SRC, caplog)


def test_forward_ref_rendered_with_caret_under_symbol(caplog: pytest.LogCaptureFixture) -> None:
    assert "  |      ^^^^^\n" in _assemble_error(PLAIN_SRC, caplog)


def test_lazy_assign_forward_ref_still_works() -> None:
    linked = link_source("P = later\nlater = 0x1234\n*=0x8000\nlda.w #P\n")
    assert linked.data == [bytes([0xA9, 0x34, 0x12])]
