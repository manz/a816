"""A `.for` variable inside an expression the linker evaluates folds to
its value.

dq6 (1.1.2): `.dw ((grid_font + k * 10 * 32) & 0xFFFF)` in a `.for`, with
`grid_font` a pooled label, failed at link: "cannot resolve `k`". The
expression went to the linker as written; dq6 unrolled the tables by hand.
"""

from __future__ import annotations

import struct
from pathlib import Path

from a816.module_builder import build_with_imports

_SOURCE = (
    ".pool fonts {{ range 0x018000 0x01ffff }}\n"
    ".pool code {{ range 0x028000 0x02ffff }}\n"
    ".alloc grid_font in fonts {{\n    .db 1, 2\n}}\n"
    ".alloc tables in code {{\n    .for k := 0, 3 {{\n        .dw {expression}\n    }}\n}}\n"
)


def _table(tmp_path: Path, expression: str) -> tuple[int, ...]:
    (tmp_path / "main.s").write_text(_SOURCE.format(expression=expression), encoding="utf-8")
    result = build_with_imports(
        main_source=tmp_path / "main.s",
        output_file=tmp_path / "out.sfc",
        output_format="sfc",
        output_dir=tmp_path / "obj",
    )
    assert result.exit_code == 0, result.diagnostics
    return struct.unpack_from("<3H", (tmp_path / "out.sfc").read_bytes(), 0x10000)


def test_the_loop_variable_folds_beside_a_pooled_label(tmp_path: Path) -> None:
    assert _table(tmp_path, "( ( grid_font + k * 10 * 32 ) & 0xFFFF )") == (0x8000, 0x8140, 0x8280)


def test_a_constant_only_loop_still_folds_at_compile_time(tmp_path: Path) -> None:
    assert _table(tmp_path, "( 0x3100 + k * 160 )") == (0x3100, 0x31A0, 0x3240)
