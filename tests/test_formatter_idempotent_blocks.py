"""Formatting is a fixed point, and it keeps what the author wrote.

Formatting cacheguard took two passes to settle: a one-line braced directive
with a trailing comment came out as `}  ; comment`, and on re-parse the comment
no longer sat on the line of a node, so pass two moved it onto its own line.
The same pass also dropped `bss` from `.pool` and rewrote `.reserve` as the
`.alloc { .res }` it desugars to. Trailing blank lines at end of file took
two passes to strip.
"""

from __future__ import annotations

import pytest

from a816.formatter import A816Formatter

_SOURCES = {
    "one_line_bss_pool": ".pool ram { bss  range 0x7e5000 0x7e5fff  strategy order }  ; scratch RAM\n",
    "reserve": (
        ".pool ram { bss  range 0x7e5000 0x7e5fff  strategy order }\n"
        ".reserve ROOM_BUF 0x1000 in ram  ; room bytecode (4 KB)\nSIZE = 0x1000\n"
    ),
    "pinned_reserve": (
        ".pool ram { bss  range 0x7e5000 0x7e5fff  strategy order }\n.reserve VEC 0x10 at 0x7e5100 in ram  ; vectors\n"
    ),
    "one_line_alloc": (
        ".pool rom { range 0x008000 0x00ffff  strategy order }\n.alloc f in rom { nop }  ; tiny routine\nX = 1\n"
    ),
    "multi_line_alloc": (
        ".pool rom { range 0x008000 0x00ffff  strategy order }\n"
        ".alloc f in rom {\n    nop\n    rts\n}  ; after the block\nX = 1\n"
    ),
    "trailing_blank_lines": "main:\n    rts\n\n\n",
}


def _fmt(src: str) -> str:
    return A816Formatter().format_text(src)


@pytest.mark.parametrize("name", sorted(_SOURCES))
def test_formatting_is_idempotent(name: str) -> None:
    once = _fmt(_SOURCES[name])
    assert _fmt(once) == once


def test_pool_keeps_bss() -> None:
    assert "    bss\n" in _fmt(_SOURCES["one_line_bss_pool"])


def test_reserve_stays_a_reserve() -> None:
    assert ".reserve ROOM_BUF 0x1000 in ram  ; room bytecode (4 KB)\n" in _fmt(_SOURCES["reserve"])


def test_pinned_reserve_keeps_its_address() -> None:
    assert ".reserve VEC 0x10 at 0x7e5100 in ram  ; vectors\n" in _fmt(_SOURCES["pinned_reserve"])


def test_output_ends_with_a_single_newline() -> None:
    assert _fmt(_SOURCES["trailing_blank_lines"]).endswith("rts\n")


def test_block_comment_stays_on_the_closing_brace() -> None:
    assert "}  ; after the block\n" in _fmt(_SOURCES["multi_line_alloc"])
