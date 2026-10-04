"""Docstring prose is not code: the formatter's line passes must leave it alone.

Comment alignment re-spaced every `;` it saw (`rooms; it` became `rooms  ; it`)
and label spacing put a blank line before any prose line ending in `:`, so
formatting rewrote documentation. Seen across cacheguard and the stdlib.
"""

from __future__ import annotations

from a816.formatter import A816Formatter

_MODULE_DOC = '''"""
Room-script DSL: authors rooms; it references the event constants.

Variadic ops stay hand-written, their payload doesn't fit a
fixed macro:
  - OP_TEXT: `op_text()` then `.text "..."`
"""
'''

_MACRO_DOC = '''.macro trig(x, off) {
    """
    Zone trigger; fires off when stepped on.
    Encoding:
      x, off
    """
    .db x
    .dw off
}
'''


def _fmt(src: str) -> str:
    return A816Formatter().format_text(src)


def test_module_docstring_keeps_semicolons() -> None:
    assert "authors rooms; it references" in _fmt(_MODULE_DOC)


def test_module_docstring_prose_colon_is_not_a_label() -> None:
    assert "doesn't fit a\nfixed macro:\n" in _fmt(_MODULE_DOC)


def test_macro_docstring_keeps_semicolons() -> None:
    assert "Zone trigger; fires off when stepped on." in _fmt(_MACRO_DOC)


def test_macro_docstring_prose_colon_is_not_a_label() -> None:
    assert "when stepped on.\n    Encoding:\n" in _fmt(_MACRO_DOC)


def test_formatting_a_docstring_is_idempotent() -> None:
    once = _fmt(_MODULE_DOC + "\n" + _MACRO_DOC)
    assert _fmt(once) == once


def test_inline_comments_outside_docstrings_still_align() -> None:
    assert "    lda #0  ; zero" in _fmt('"""Doc; with semicolon."""\nmain:\n    lda #0 ;zero\n    rts\n')


def test_semicolon_inside_a_string_is_not_a_comment() -> None:
    assert '.ascii "a;b"' in _fmt('.ascii "a;b"\n')
