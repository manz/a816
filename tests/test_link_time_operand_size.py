"""An unsized operand naming a link-time symbol must spell its size.

`jmp target` with `target` placed by another module evaluates to 0 at
compile time, so it was sized `.b` and rejected with "jmp does not supports
size (b)". The right form depends on where the linker puts `target` (`.w`
in the same bank, `.l` across), so it is E0313 with the forms to pick from;
register-sized immediates and single-form opcodes stay decidable.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tests import BANK_40_MAP, build_rom

_LIB = ".alloc at 0x408100 {\ntarget:\n    rts\n}\n"


def _build(tmp_path: Path, body: str, caplog: pytest.LogCaptureFixture) -> tuple[int, bytes, str]:
    files = {"main.s": BANK_40_MAP + '.import "lib"\n.alloc at 0x408000 {\n' + body + "}\n", "lib.s": _LIB}
    with caplog.at_level(logging.ERROR):
        rc, rom = build_rom(tmp_path, files)
    return rc, rom, caplog.text


@pytest.mark.parametrize(
    ("line", "forms"),
    [
        ("jmp target", "`jmp.w` or `jmp.l`"),
        ("jsr target", "`jsr.w` or `jsr.l`"),
        ("lda target", "`lda.b` or `lda.w` or `lda.l`"),
    ],
)
def test_an_unsized_extern_operand_asks_for_its_size(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, line: str, forms: str
) -> None:
    rc, _rom, log = _build(tmp_path, f"    {line}\n", caplog)
    assert rc != 0
    assert "E0313" in log
    assert f"write the size: {forms}" in log


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("jmp.w target", "4c 00 81"),
        ("jmp.l target", "5c 00 81 40"),
        ("jsr.w target", "20 00 81"),
        ("pea target", "f4 00 81"),
        ("lda #target", "a9 00"),
    ],
)
def test_sized_or_single_form_extern_operands_build(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, line: str, expected: str
) -> None:
    rc, rom, _log = _build(tmp_path, f"    {line}\n", caplog)
    assert rc == 0
    assert rom[0x8000 : 0x8000 + len(expected.split())].hex(" ") == expected
