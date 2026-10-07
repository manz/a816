"""`a816 format` keeps comments and blank lines inside `.struct` bodies.

Both own-line comments and trailing `; note` comments on field lines were
dropped (ff4 lost field history, Bahamut Lagoon moved its notes into the
docstring to dodge it).
"""

from __future__ import annotations

import pytest

from a816.formatter import A816Formatter
from a816.parse.ast.nodes import StructAstNode
from a816.parse.mzparser import A816Parser

_SOURCE = """.struct T {
    ; field note
    byte a      ; trailing a
    word b

    ; second group
    u4 lo  ; nibble
    u4 hi
    /* closing note */
}
"""
_EXPECTED = """.struct T {
    ; field note
    byte a  ; trailing a
    word b

    ; second group
    u4 lo  ; nibble
    u4 hi
    /* closing note */
}
"""


def _format(source: str) -> str:
    return A816Formatter().format_text(source)


def test_struct_comments_and_groups_survive_formatting() -> None:
    assert _format(_SOURCE) == _EXPECTED


def test_formatting_struct_comments_is_a_fixed_point() -> None:
    assert _format(_EXPECTED) == _EXPECTED


@pytest.mark.parametrize("source", [".struct T {\n    byte a\n    word b\n}\n", ".struct T {\n}\n"])
def test_a_struct_without_comments_is_unchanged(source: str) -> None:
    assert _format(source) == source


def test_comments_do_not_change_the_layout() -> None:
    result = A816Parser.parse_as_ast(_SOURCE, filename="t.s")
    assert result.parse_error is None
    struct = result.nodes[0]
    assert isinstance(struct, StructAstNode)
    assert struct.fields == (("a", "byte"), ("b", "word"), ("lo", "u4"), ("hi", "u4"))


def test_a_comment_continued_under_a_trailing_comment_stays_aligned() -> None:
    source = (
        ".struct T {\n"
        "    word remap_tmp  ; scratch, must not be pass_flag\n"
        "                    ; (clobbering it forced rescans)\n"
        "    byte b\n"
        "}\n"
    )
    assert _format(source) == source
