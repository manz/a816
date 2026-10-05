"""Register width sizes immediates only, never memory operands.

Under `.a16` / `.i16` (or a tracked `rep`), `lda 0x12` used to emit absolute
`AD 12 00` instead of direct page `A5 12`: absolute reads `DB:0012`, direct
page reads `D+0x12`, which differ whenever D is not 0.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.module_builder import build_with_imports

_MAP = ".map identifier=1 bank_range=0x40, 0x7d addr_range=0x0000, 0xffff mask=0x10000\n"
_WIDTHS = [".a8\n    .i8", ".a16\n    .i16", "rep #0x30", "sep #0x30"]

# (instruction, bytes) whatever the A/X width.
_MEMORY = [
    ("lda 0x12", "a5 12"),
    ("lda 0x1234", "ad 34 12"),
    ("lda 0x123456", "af 56 34 12"),
    ("lda 0x12, x", "b5 12"),
    ("ora 0x12", "05 12"),
    ("ora 0x12, x", "15 12"),
    ("cmp 0x12", "c5 12"),
    ("cmp 0x12, x", "d5 12"),
    ("ldx 0x12", "a6 12"),
    ("ldx 0x12, y", "b6 12"),
    ("ldy 0x12", "a4 12"),
    ("ldy 0x12, x", "b4 12"),
    ("adc 0x12", "65 12"),
]


def _emit(tmp_path: Path, width: str, line: str) -> str:
    (tmp_path / "main.s").write_text(
        _MAP + ".alloc c at 0x400000 {\n    " + width + "\n    " + line + "\n    .db 0xEE\n}\n"
    )
    out = tmp_path / "out.sfc"
    result = build_with_imports(
        tmp_path / "main.s",
        out,
        output_format="sfc",
        output_dir=tmp_path / "obj",
        use_a816_toml=False,
        experimental=["track_register_size"],
    )
    assert result.exit_code == 0
    rom = out.read_bytes()
    width_prefix = 2 if width.startswith(("rep", "sep")) else 0
    return rom[width_prefix : rom.index(0xEE, width_prefix)].hex(" ")


@pytest.mark.parametrize("width", _WIDTHS)
@pytest.mark.parametrize(("line", "expected"), _MEMORY)
def test_memory_operand_size_ignores_register_width(tmp_path: Path, width: str, line: str, expected: str) -> None:
    assert _emit(tmp_path, width, line) == expected


@pytest.mark.parametrize(
    ("width", "line", "expected"),
    [
        (".a16\n    .i16", "lda #0x12", "a9 12 00"),
        (".a8\n    .i8", "lda #0x12", "a9 12"),
        (".a16\n    .i16", "ldx #0x12", "a2 12 00"),
        ("rep #0x30", "ldy #0x12", "a0 12 00"),
    ],
)
def test_immediates_still_follow_register_width(tmp_path: Path, width: str, line: str, expected: str) -> None:
    assert _emit(tmp_path, width, line) == expected
