"""Unary `<` / `>`: the low and high byte of a word, as in other 6502-family assemblers."""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.cli import build_main
from a816.formatter import A816Formatter
from a816.module_builder import build_with_imports
from a816.parse.ast.expression import eval_constant_expression


@pytest.mark.parametrize(
    ("expression", "value"),
    [
        ("<0x1234", 0x34),
        (">0x1234", 0x12),
        (">0x123456", 0x34),
        ("<0x1234 + 1", 0x35),
        ("<(0x12FF + 1)", 0x00),
        ("0x10 + >0x1234", 0x22),
        ("-3 * 2", -6),
        ("1 < 2", 1),
        ("4 > 2", 1),
    ],
)
def test_the_byte_operators_bind_tightest(expression: str, value: int) -> None:
    assert eval_constant_expression(expression) == value


def test_an_spc700_immediate_takes_a_byte_of_a_label(tmp_path: Path) -> None:
    source = tmp_path / "d.spc.s"
    source.write_text(".alloc d at 0x0400 {\ntable:\n    mov a, #<table\n    mov y, #>table\n}\n", encoding="utf-8")
    output = tmp_path / "d.aram"
    result = build_with_imports(source, output, output_format="aram", output_dir=tmp_path / "obj", use_a816_toml=False)
    assert (result.exit_code, output.read_bytes()) == (0, bytes([0xE8, 0x00, 0x8D, 0x04]))


def test_the_linker_takes_a_byte_of_an_extern(tmp_path: Path) -> None:
    (tmp_path / "main.s").write_text(
        '.import "data"\n.alloc main at 0x008000 {\n    lda #<table\n    lda #>table\n}\n', encoding="utf-8"
    )
    (tmp_path / "data.s").write_text(".alloc data at 0x00A000 {\ntable:\n    .db 0\n}\n", encoding="utf-8")
    output = tmp_path / "out.sfc"
    argv = [str(tmp_path / "main.s"), "-f", "sfc", "-o", str(output), "--obj-dir", str(tmp_path / "obj")]
    assert build_main(argv) == 0
    assert output.read_bytes()[:4] == bytes([0xA9, 0x00, 0xA9, 0xA0])


def test_a_byte_operator_formats_against_its_operand() -> None:
    formatted = A816Formatter().format_text(".alloc d at 0x0400 {\n    mov y,#< t\n}\n", "d.spc.s")
    assert "    mov y, #<t\n" in formatted
