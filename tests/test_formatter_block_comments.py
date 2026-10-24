"""Comments keep their place when `a816 format` re-indents a block.

thg (1.2.0a2): inside a block, a label with a trailing comment was
indented like an instruction, a comment hanging under a trailing comment
lost its column, a comment trailing a block's `{` moved into the body,
and an indented comment in a body went back to column 0. A line whose
trailing comment ended in `:` went flush-left like a label.
"""

from __future__ import annotations

import pytest

from a816.formatter import A816Formatter


def _fmt(src: str) -> str:
    return A816Formatter().format_text(src)


_FORMATTED = {
    "label_with_comment_in_block": ".alloc at 0x828000 size 8 {\n_init:  ; M16 X16\n    rts\n\n_done:\n    rts\n}\n",
    "hanging_comment_top_level": "_base:  ; first part\n        ; second part\n    rts\n",
    "hanging_comment_chain": "_base:  ; first part\n        ; second part\n        ; third part\n    rts\n",
    "hanging_comment_in_block": ".alloc at 0x828000 size 8 {\n    lda #1  ; load\n            ; and more\n    rts\n}\n",
    "hanging_comment_under_label_in_block": ".alloc at 0x828000 size 8 {\n_base:  ; first\n        ; second\n    rts\n}\n",
    "hanging_comment_in_nested_block": ".if 1 {\n    .if 1 {\n        nop  ; one\n             ; two\n    }\n}\n",
    "alloc_header_comment": ".alloc at 0x82A5C8 size 2 {  ; Poison\n    .dw 0\n}\n",
    "if_header_comment": ".if 1 {  ; always\n    nop\n} else {  ; never\n    rts\n}\n",
    "macro_header_comment": '"""M."""\n.macro m() {  ; no args\n    nop\n}\n',
    "for_header_comment": ".for k := 0, 2 {  ; twice\n    nop\n}\n",
    "indented_comment_in_if": ".if 1 {\n    ; inside if\n    nop\n}\n",
    "indented_comment_in_nested_if": ".if 1 {\n    .if 1 {\n        ; deep\n        nop\n    }\n}\n",
    "flush_comment_in_if": ".if 1 {\n; banner\n    nop\n}\n",
    "scope_header_comment": ".scope s {  ; note\n    nop\n}\n",
    "bare_block_header_comment": ".if 1 {\n    {  ; inner\n        nop\n    }\n}\n",
    "hanging_comment_in_if_after_label": "main:\n.if 1 {\n_lbl:  ; one\n       ; two\n    nop\n}\n",
    # ff4 / feda: a trailing comment ending in `:` made the line a label.
    "colon_comment_in_block": ".alloc at 0x828000 size 8 {\n    X := 1  ; rows:\n    nop\n}\n",
}


@pytest.mark.parametrize("name", sorted(_FORMATTED))
def test_formatted_source_is_kept(name: str) -> None:
    assert _fmt(_FORMATTED[name]) == _FORMATTED[name]


def test_label_with_comment_in_block_stays_flush_left() -> None:
    src = ".alloc at 0x828000 size 8 {\n    _init:  ; M16 X16\n    rts\n}\n"
    assert "\n_init:  ; M16 X16\n" in _fmt(src)


def test_hanging_comment_follows_the_realigned_comment() -> None:
    assert _fmt("_base: ; first part\n       ; second part\n    rts\n") == _FORMATTED["hanging_comment_top_level"]


def test_hanging_comment_in_block_follows_the_body_indent() -> None:
    src = ".alloc at 0x828000 size 8 {\nlda #1 ; load\n       ; and more\nrts\n}\n"
    assert _fmt(src) == _FORMATTED["hanging_comment_in_block"]


def test_comment_on_its_own_line_after_a_header_stays_in_the_body() -> None:
    src = ".alloc at 0x82A5C8 size 2 {\n    ; Poison\n    .dw 0\n}\n"
    assert _fmt(src) == src


def test_unaligned_comment_after_a_trailing_comment_is_not_hanging() -> None:
    src = "_base:  ; first part\n    ; body note\n    rts\n"
    assert _fmt(src) == src
