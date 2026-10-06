"""A comment ending in `:` is prose, not a label: `a816 format` keeps the comment block whole."""

from a816.formatter import A816Formatter


def _fmt(src: str) -> str:
    return A816Formatter().format_text(src)


_BLOCK = "A = 1\n\n; first line\n; count, glyph, shift, rows:\n.struct S {\n    byte a\n}\n"


def test_comment_ending_in_a_colon_stays_in_its_block() -> None:
    assert _fmt(_BLOCK) == _BLOCK


def test_code_then_comment_ending_in_a_colon_is_not_a_label() -> None:
    src = "main:\n    lda #0\n    nop  ; then:\n    rts\n"
    assert _fmt(src) == src


def test_a_brace_in_a_comment_does_not_hide_top_level_labels() -> None:
    src = "; skips the { of the table\nfirst:\n    rts\nsecond:\n    rts\n"
    assert "    rts\n\nsecond:\n" in _fmt(src)


def test_a_bare_label_still_gets_its_blank_line() -> None:
    assert "    rts\n\nsecond:\n" in _fmt("first:\n    rts\nsecond:\n    rts\n")
