"""`.assert EXPR, "message"` checked at link time.

Layout contracts (alignment, a blob inside a gap, a table within one bank)
belong next to the allocs, and must see final addresses: the expression
travels in the object and the linker evaluates it after placement, with the
module's own private labels in scope. Every failure is reported at once.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from a816.exceptions import LinkAssertError
from a816.formatter import A816Formatter
from a816.linker import Linker
from a816.object_file import ObjectFile
from a816.parse.mzparser import A816Parser
from a816.program import Program

_SRC = (
    ".map identifier=1 bank_range=0x40, 0x7d addr_range=0x0000, 0xffff mask=0x10000\n"
    ".pool g { range 0x500010 0x50ffff }\n"
    ".alloc a in g {\n    .db 1\n}\n"
)


def _link(src: str) -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "m.s").write_text(src)
        assert Program().assemble_as_object(str(tmp / "m.s"), tmp / "m.o") == 0
        Linker([ObjectFile.from_file(str(tmp / "m.o"))]).link(base_address=0x8000)


def test_a_true_assert_links() -> None:
    _link(_SRC + '.assert a == 0x500010, "a sits at the pool start"\n')


def test_a_false_assert_fails_with_message_expression_and_location() -> None:
    with pytest.raises(LinkAssertError) as excinfo:
        _link(_SRC + '.assert (a & 0xFFFF) == 0, "a must be bank-aligned"\n')
    formatted = excinfo.value.format()
    assert "E0407" in formatted
    assert "a must be bank-aligned" in formatted
    assert "( a & 0xFFFF ) == 0" in formatted
    assert "m.s:6" in formatted


def test_every_failed_assert_is_reported() -> None:
    with pytest.raises(LinkAssertError) as excinfo:
        _link(_SRC + '.assert a == 0, "first"\n.assert a == 1, "second"\n')
    assert [message for message, _, _ in excinfo.value.failures] == ["first", "second"]


def test_an_assert_sees_its_module_s_private_labels() -> None:
    _link(
        ".map identifier=1 bank_range=0x40, 0x7d addr_range=0x0000, 0xffff mask=0x10000\n"
        ".pool g { range 0x500000 0x50ffff }\n"
        ".alloc _private in g {\n    .db 1\n}\n"
        '.assert _private == 0x500000, "private"\n'
    )


def test_an_assert_round_trips_through_the_object(tmp_path: Path) -> None:
    (tmp_path / "m.s").write_text(_SRC + '.assert a > 0, "positive"\n')
    assert Program().assemble_as_object(str(tmp_path / "m.s"), tmp_path / "m.o") == 0
    (check,) = ObjectFile.from_file(str(tmp_path / "m.o")).asserts
    assert (check.expression, check.message) == ("a > 0", "positive")
    assert check.source.endswith("m.s:6")


def test_parse_only_runs_skip_asserts() -> None:
    result = A816Parser.parse_as_ast('.assert missing == 1, "never evaluated here"\n', filename="t.s")
    assert result.parse_error is None


def test_formatter_keeps_asserts() -> None:
    src = '.assert (a & 0xFFFF) == 0, "msg"\n'
    assert '.assert ( a & 0xFFFF ) == 0, "msg"\n' in A816Formatter().format_text(src)
