"""Typed access over an `.extern` keeps the struct field offset.

`(tbl as S).field` and `view := (tbl as S)` with `tbl` placed by another
module must lower to `tbl + S.field` at link. Both used to drop the offset
(or refuse the bind) without a diagnostic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import BANK_40_MAP, build_rom

_STRUCT = ".struct S {\n    word a\n    byte b\n}\n"
_MAIN = BANK_40_MAP + '.import "user"\n.alloc tbl at 0x410000 {\n    .db 1, 2, 3\n}\n'


def _emit(tmp_path: Path, user_body: str) -> str:
    rc, rom = build_rom(tmp_path, {"main.s": _MAIN, "user.s": _STRUCT + ".extern tbl\n" + user_body})
    assert rc == 0
    return rom[:4].hex(" ")


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("    lda.l (tbl as S).b, x\n", "bf 02 00 41"),
        ("    lda.l (tbl as S).b\n", "af 02 00 41"),
        ("    lda.l (tbl as S).a, x\n", "bf 00 00 41"),
    ],
)
def test_an_inline_cast_over_an_extern_keeps_the_field_offset(tmp_path: Path, body: str, expected: str) -> None:
    assert _emit(tmp_path, ".alloc code at 0x400000 {\n" + body + "}\n") == expected


@pytest.mark.parametrize(
    ("operand", "expected"),
    [
        ("v.b, x", "bf 02 00 41"),
        ("v.b", "af 02 00 41"),
        ("v, x", "bf 00 00 41"),
    ],
)
def test_a_typed_view_over_an_extern_binds_at_link(tmp_path: Path, operand: str, expected: str) -> None:
    user = "v := (tbl as S)\n.alloc code at 0x400000 {\n    lda.l " + operand + "\n}\n"
    assert _emit(tmp_path, user) == expected
