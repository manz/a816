"""Typed access over an `.extern` keeps the struct field offset.

`(tbl as S).field` and `view := (tbl as S)` with `tbl` placed by another
module must lower to `tbl + S.field` at link. Both used to drop the offset
(or refuse the bind) without a diagnostic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.module_builder import build_with_imports

_MAP = ".map identifier=1 bank_range=0x40, 0x7d addr_range=0x0000, 0xffff mask=0x10000\n"
_STRUCT = ".struct S {\n    word a\n    byte b\n}\n"
_MAIN = _MAP + '.import "user"\n.alloc tbl at 0x410000 {\n    .db 1, 2, 3\n}\n'


def _emit(tmp_path: Path, user_body: str) -> bytes:
    (tmp_path / "main.s").write_text(_MAIN)
    (tmp_path / "user.s").write_text(_STRUCT + ".extern tbl\n" + user_body)
    out = tmp_path / "out.sfc"
    result = build_with_imports(
        tmp_path / "main.s", out, output_format="sfc", output_dir=tmp_path / "obj", use_a816_toml=False
    )
    assert result.exit_code == 0
    return out.read_bytes()[:4]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("    lda.l (tbl as S).b, x\n", "bf 02 00 41"),
        ("    lda.l (tbl as S).b\n", "af 02 00 41"),
        ("    lda.l (tbl as S).a, x\n", "bf 00 00 41"),
    ],
)
def test_an_inline_cast_over_an_extern_keeps_the_field_offset(tmp_path: Path, body: str, expected: str) -> None:
    assert _emit(tmp_path, ".alloc code at 0x400000 {\n" + body + "}\n").hex(" ") == expected


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
    assert _emit(tmp_path, user).hex(" ") == expected
