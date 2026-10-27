"""Nested bodies keep their nesting when formatted.

ff6 (1.1.3): inside an `.alloc`, `.if` / `.for` bodies came out at the
alloc's indent, and a macro block argument `ram_patch(0x7E5CFB, { .dw x })`
as `.dw x}` with `)` on the next line. Every re-indent stripped each line
down to its text, so whatever nesting the inner block had was lost.
"""

from __future__ import annotations

import pytest

from a816.formatter import A816Formatter
from tests import link_source

_MACRO = '"""M."""\n.macro patch(addr, body) {\n    {{ body }}\n}\n'

_STRUCT = ".struct S {\n    byte a\n}\n"

_SOURCES = {
    "if_in_alloc": ".alloc at 0x008000 {\n.if 1 {\nnop\n} else {\nrts\n}\n}\n",
    "for_in_alloc": ".alloc at 0x008000 {\n.for k := 0, 2 {\nnop\n}\n}\n",
    "if_in_if": ".if 1 {\n.if 1 {\nnop\n}\n}\n",
    "bare_block_in_alloc": ".alloc at 0x008000 {\n{\nnop\n}\n}\n",
    "block_argument": ".alloc at 0x008000 {\npatch(0x7E5CFB, { .dw 1 })\n}\n",
    "block_argument_lines": ".alloc at 0x008000 {\npatch(0x7E5CFB, {\n.dw 1\n.dw 2\n})\n}\n",
    "istruct_in_alloc": _STRUCT + ".alloc at 0x008000 {\n.db 5\n.istruct S {\na = 1\n}\n}\n",
    "istruct_in_nested_alloc": _STRUCT + "{\n.alloc at 0x008000 {\nlabel:\n.db 5\n.istruct S {\na = 1\n}\n}\n}\n",
}

_EXPECTED = {
    "if_in_alloc": ".alloc at 0x008000 {\n    .if 1 {\n        nop\n    } else {\n        rts\n    }\n}\n",
    "for_in_alloc": ".alloc at 0x008000 {\n    .for k := 0, 2 {\n        nop\n    }\n}\n",
    "if_in_if": ".if 1 {\n    .if 1 {\n        nop\n    }\n}\n",
    "bare_block_in_alloc": ".alloc at 0x008000 {\n    {\n        nop\n    }\n}\n",
    "block_argument": ".alloc at 0x008000 {\n    patch(0x7E5CFB, {\n        .dw 1\n    })\n}\n",
    "block_argument_lines": ".alloc at 0x008000 {\n    patch(0x7E5CFB, {\n        .dw 1\n        .dw 2\n    })\n}\n",
    "istruct_in_alloc": _STRUCT + ".alloc at 0x008000 {\n    .db 5\n    .istruct S {\n        a = 1\n    }\n}\n",
    # Bahamut Lagoon title_screen.s: the `.istruct` stayed one level out, under `.db`.
    "istruct_in_nested_alloc": _STRUCT
    + "{\n    .alloc at 0x008000 {\nlabel:\n        .db 5\n        .istruct S {\n            a = 1\n        }\n    }\n}\n",
}


def _fmt(src: str) -> str:
    return A816Formatter().format_text(_MACRO + src)


def _assemble(src: str) -> bytes:
    """The bytes `src` builds to; `*=` gives the sources without an `.alloc` a place."""
    linked = link_source("*=0x008000\n" + src)
    assert linked.exit_code == 0
    assert linked.data
    return b"".join(linked.data)


@pytest.mark.parametrize("name", sorted(_SOURCES))
def test_body_is_nested(name: str) -> None:
    assert _fmt(_SOURCES[name]) == _MACRO + _EXPECTED[name]


@pytest.mark.parametrize("name", sorted(_SOURCES))
def test_one_pass_converges(name: str) -> None:
    once = _fmt(_SOURCES[name])

    assert A816Formatter().format_text(once) == once


@pytest.mark.parametrize("name", sorted(_SOURCES))
def test_formatting_keeps_the_bytes(name: str) -> None:
    assert _assemble(_fmt(_SOURCES[name])) == _assemble(_MACRO + _SOURCES[name])


def test_block_argument_between_values() -> None:
    src = '"""M."""\n.macro m(a, body, b) {\n    {{ body }}\n}\nm(1, { nop }, 2)\n'

    assert "m(1, {\n    nop\n}, 2)\n" in A816Formatter().format_text(src)
