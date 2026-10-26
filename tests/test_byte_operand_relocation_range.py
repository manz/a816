"""A one-byte operand filled at link must hold the value it names.

The linker used to mask a 1-byte relocation to its low byte: `lda.b far`
with `far` at $01:8000 assembled to `lda.b $00`. A `.b` immediate now
follows the compile-time rule (-0x100..0xFF) and a direct-page operand
must name an address the direct page can reach.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from a816.exceptions import ByteOperandError
from a816.linker import Linker, byte_operand_problem
from a816.module_builder import build_with_imports
from a816.object_file import (
    BYTE_IMMEDIATE_PREFIX,
    DIRECT_PAGE_PREFIX,
    DIRECT_POINTER_PREFIX,
    STACK_OFFSET_PREFIX,
    ObjectFile,
    SymbolSection,
    SymbolType,
)

POOLS = """\
.pool rom {
    range 0x018000 0x01ffff
    strategy order
}
.pool zp {
    bss
    range 0x7e0010 0x7e00ff
    strategy order
}
.pool wram {
    bss
    range 0x7e2000 0x7effff
    strategy order
}
.reserve low 2 in zp
.reserve high 2 in wram
.alloc far_code in rom {
far:
    rts
}
"""


def _build(tmp_path: Path, caplog: pytest.LogCaptureFixture, body: str) -> tuple[int, str]:
    (tmp_path / "a816.toml").write_text('board = "SHVC-1J0N-20"\nrom_size = 0x80000\n', encoding="utf-8")
    (tmp_path / "m.s").write_text(POOLS, encoding="utf-8")
    (tmp_path / "main.s").write_text(f'.import "m"\n.alloc code at 0x008000 {{\n{body}\n}}\n', encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        result = build_with_imports(
            tmp_path / "main.s",
            tmp_path / "x.sfc",
            module_paths=[tmp_path],
            output_dir=tmp_path / "obj",
            output_format="sfc",
        )
    return result.exit_code, re.sub(r"\x1b\[[0-9;]*m", "", caplog.text)


def test_a_direct_page_operand_naming_rom_is_refused(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _, text = _build(tmp_path, caplog, "    lda.b far")

    assert "error[E0402]: `far` = $018000 is not reachable through the direct page" in text


def test_the_refusal_names_the_source_line(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _, text = _build(tmp_path, caplog, "    nop\n    lda.b far")

    assert "main.s:4" in text


def test_the_refusal_says_what_to_write(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _, text = _build(tmp_path, caplog, "    lda.b far")

    assert "write the opcode with `.w` or `.l`" in text


def test_a_direct_page_operand_outside_low_ram_is_refused(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert _build(tmp_path, caplog, "    lda.b high")[0] != 0


def test_a_direct_page_operand_in_low_ram_builds(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert _build(tmp_path, caplog, "    lda.b low\n    sta.b low + 1")[0] == 0


@pytest.mark.parametrize("operand", ["(far)", "(far,x)", "(far),y", "[far]", "[far],y", "far,x"])
def test_every_direct_page_form_is_checked(tmp_path: Path, caplog: pytest.LogCaptureFixture, operand: str) -> None:
    _, text = _build(tmp_path, caplog, f"    lda.b {operand}")

    assert "is not reachable through the direct page" in text


@pytest.mark.parametrize("operand", ["(far)", "(far,x)", "(far),y", "[far]", "[far],y"])
def test_an_indirect_operand_is_told_to_move_its_pointer(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, operand: str
) -> None:
    _, text = _build(tmp_path, caplog, f"    lda.b {operand}")

    assert "keep the pointer in bank 0 RAM" in text


@pytest.mark.parametrize("operand", ["far,s", "(far,s),y"])
def test_a_stack_offset_that_does_not_fit_is_refused(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, operand: str
) -> None:
    _, text = _build(tmp_path, caplog, f"    lda {operand}")

    assert "error[E0402]: `far` = $018000 is not a stack offset (0..0xFF)" in text


def test_a_stack_offset_refusal_says_it_is_an_offset(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _, text = _build(tmp_path, caplog, "    lda far,s")

    assert "one-byte offset from S" in text


def test_a_byte_immediate_that_does_not_fit_is_refused(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _, text = _build(tmp_path, caplog, "    sep #0x20\n    .a8\n    lda.b #far")

    assert "error[E0402]: `far` = $018000 does not fit in a byte" in text


@pytest.mark.parametrize("operand", ["#<far", "#>far", "#far >> 16", "#far & 0xFF"])
def test_a_byte_immediate_that_fits_builds(tmp_path: Path, caplog: pytest.LogCaptureFixture, operand: str) -> None:
    assert _build(tmp_path, caplog, f"    sep #0x20\n    .a8\n    lda.b {operand}")[0] == 0


def test_a_word_immediate_keeps_masking(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert _build(tmp_path, caplog, "    lda.w #far")[0] == 0


@pytest.mark.parametrize("value", [0x00, 0xFF, -0x100, -1])
def test_byte_immediates_in_range(value: int) -> None:
    assert byte_operand_problem(BYTE_IMMEDIATE_PREFIX, value) is None


@pytest.mark.parametrize("value", [0x100, -0x101, 0x018000])
def test_byte_immediates_out_of_range(value: int) -> None:
    assert byte_operand_problem(BYTE_IMMEDIATE_PREFIX, value) is not None


@pytest.mark.parametrize("value", [0x10, 0x7E0010, 0x7E1FFF, 0x002100, 0x804300, 0x3F0010])
def test_direct_page_reachable(value: int) -> None:
    assert byte_operand_problem(DIRECT_PAGE_PREFIX, value) is None


@pytest.mark.parametrize("value", [0x018000, 0x008000, 0x7E2000, 0x7F0010, 0x400010, 0xC00010, -1])
def test_direct_page_unreachable(value: int) -> None:
    assert byte_operand_problem(DIRECT_PAGE_PREFIX, value) is not None


def _link(lines: list[tuple[int, int, int, int, int]], files: list[str]) -> ByteOperandError:
    obj = ObjectFile(
        b"\xa5\x00",
        [("far", 0x018000, SymbolType.GLOBAL, SymbolSection.CODE)],
        expression_relocations=[(1, DIRECT_PAGE_PREFIX + "far", 1)],
        files=files,
        lines=lines,
        relocatable=False,
    )
    with pytest.raises(ByteOperandError) as caught:
        Linker([obj]).link()
    return caught.value


def test_an_object_without_lines_is_refused_without_a_location() -> None:
    assert _link([], []).location is None


def test_a_line_past_the_file_table_gives_no_location() -> None:
    assert _link([(0, 3, 0, 0, 0)], []).location is None


@pytest.mark.parametrize(("value", "fits"), [(0, True), (0xFF, True), (0x100, False), (-1, False)])
def test_stack_offsets(value: int, fits: bool) -> None:
    assert (byte_operand_problem(STACK_OFFSET_PREFIX, value) is None) is fits


@pytest.mark.parametrize("value", [0x7E0010, 0x018000])
def test_a_pointer_follows_the_direct_page_rule(value: int) -> None:
    assert byte_operand_problem(DIRECT_POINTER_PREFIX, value) == byte_operand_problem(DIRECT_PAGE_PREFIX, value)
