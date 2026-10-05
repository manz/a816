"""Direct (non-object) builds honour `.assert` and `cross_bank`.

Both are link-time features; a direct build has no linker, so `.assert`
went unchecked (a false one exited 0) and pools never learnt the bus, so a
`cross_bank` alloc overflowed with a hint to add `cross_bank`.
"""

from __future__ import annotations

from pathlib import Path

from a816.program import Program
from tests import BANK_40_MAP

_POOL = ".pool g { range 0x500000 0x52ffff }\n"


def _assemble(tmp_path: Path, source: str) -> int:
    (tmp_path / "m.s").write_text(BANK_40_MAP + source)
    return Program().assemble(str(tmp_path / "m.s"), tmp_path / "m.sfc")


def test_a_false_assert_fails_a_direct_build(tmp_path: Path) -> None:
    assert _assemble(tmp_path, _POOL + '.alloc a in g {\n    .db 1\n}\n.assert a == 0, "must fail"\n') == 128


def test_a_true_assert_over_a_pooled_label_passes(tmp_path: Path) -> None:
    assert _assemble(tmp_path, _POOL + '.alloc a in g {\n    .db 1\n}\n.assert a == 0x500000, "pool start"\n') == 0


def test_an_assert_naming_an_undefined_symbol_fails(tmp_path: Path) -> None:
    assert _assemble(tmp_path, '.assert missing == 0, "needs missing"\n') == 128


def test_a_cross_bank_alloc_spans_banks_in_a_direct_build(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"\x01" * 0x18000)
    source = _POOL + f'.alloc blob in g cross_bank {{\n    .incbin "{tmp_path / "blob.bin"}"\n}}\n'
    assert _assemble(tmp_path, source) == 0
    rom = (tmp_path / "m.sfc").read_bytes()
    assert rom[0x100000 : 0x100000 + 0x18000] == b"\x01" * 0x18000
