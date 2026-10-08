"""A macro forwarding its own parameter to an inner macro, with a link-time argument.

cacheguard (a51): `outer(thing)` with `thing` a `.reserve` and the inner
macro computing `p >> 16` recursed forever ("maximum recursion depth
exceeded"), and renaming the inner parameter gave E0200 instead: the
inner macro got the name `p`, resolved after `outer`'s binding was gone.
Fixed by a52; these pin it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.module_builder import build_with_imports

_SOURCE = """\
.pool engine {{
    range 0xc10000 0xc1ffff
    strategy order
}}
.pool wram {{
    bss
    range 0x7e2000 0x7effff
    strategy order
}}
.reserve thing 4 in wram
.macro inner({inner}) {{
    lda #( {inner} >> 16 )
}}
.macro outer(p) {{
    inner(p)
}}
.alloc code in engine {{
    outer(thing)
}}
"""


def _rom(tmp_path: Path, inner: str) -> bytes:
    (tmp_path / "a816.toml").write_text('board = "SHVC-1J0N-20"\nrom_size = 0x80000\n', encoding="utf-8")
    main = tmp_path / "main.s"
    main.write_text(_SOURCE.format(inner=inner), encoding="utf-8")
    result = build_with_imports(main, tmp_path / "x.sfc", output_dir=tmp_path / "obj", output_format="sfc")
    assert result.exit_code == 0
    return (tmp_path / "x.sfc").read_bytes()


@pytest.mark.parametrize("inner", ["p", "q"], ids=["same-name", "renamed"])
def test_the_inner_macro_sees_the_outer_argument(tmp_path: Path, inner: str) -> None:
    """`lda #$7E`: the bank of `thing`, placed at $7E:2000."""
    assert _rom(tmp_path, inner)[0x10000:0x10002] == b"\xa9\x7e"
