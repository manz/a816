"""`:=` evaluates at codegen: forward refs get a located E0210 diagnostic."""

from __future__ import annotations

import pytest

from a816.parse.nodes import NodeError
from a816.program import Program
from tests import StubWriter

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


def _assemble_error(src: str) -> NodeError:
    program = Program()
    writer = StubWriter()
    with pytest.raises(NodeError) as exc_info:
        program.assemble_string_with_emitter(src, "fwd.s", writer)
    return exc_info.value


@pytest.mark.parametrize("src", [PLAIN_SRC, TYPED_SRC, NESTED_SRC, CAST_THEN_REF_SRC])
def test_forward_ref_carries_code(src: str) -> None:
    assert _assemble_error(src).code == "E0210"


@pytest.mark.parametrize("src", [PLAIN_SRC, TYPED_SRC, NESTED_SRC, CAST_THEN_REF_SRC])
def test_forward_ref_points_at_symbol_token(src: str) -> None:
    error = _assemble_error(src)
    assert error.file_info is not None
    assert error.file_info.value == "later"


def test_plain_forward_ref_caret_column() -> None:
    error = _assemble_error(PLAIN_SRC)
    assert error.file_info is not None
    assert error.file_info.position is not None
    assert error.file_info.position.column == len("P := ")


def test_typed_forward_ref_caret_line() -> None:
    error = _assemble_error(TYPED_SRC)
    assert error.file_info is not None
    assert error.file_info.position is not None
    assert error.file_info.position.line == 3


def test_forward_ref_hint_suggests_lazy_assign() -> None:
    error = _assemble_error(PLAIN_SRC)
    assert error.hint is not None
    assert "use `=` for a forward reference" in error.hint


def test_forward_ref_message_names_symbol() -> None:
    assert "`later`" in _assemble_error(PLAIN_SRC).message


def test_forward_ref_rendered_with_caret_under_symbol() -> None:
    rendered = str(_assemble_error(PLAIN_SRC))
    assert "  |      ^^^^^\n" in rendered


def test_lazy_assign_forward_ref_still_works() -> None:
    program = Program()
    writer = StubWriter()
    program.assemble_string_with_emitter("P = later\nlater = 0x1234\n*=0x8000\nlda.w #P\n", "lazy.s", writer)
    assert writer.data == [bytes([0xA9, 0x34, 0x12])]
