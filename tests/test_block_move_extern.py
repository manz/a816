"""A `mvn` / `mvp` operand naming a symbol placed at link gets its value at link.

Bahamut Lagoon (rc3): `mvn blob_start >> 16, 0x7E`, with `blob_start` an
`.extern` from another module's pooled alloc, emitted `54 7E 00`: the
block-move path read the operand's compile-time value and recorded no
relocation, so the source bank was 0 and every baked name copied from bank
$00. `lda.l blob_start` in the same object was right.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.module_builder import build_with_imports

DATA = ".pool far { range 0x018000 0x01ffff  strategy order }\n.alloc blob in far {\nblob_start:\n    .db 1, 2, 3\n}\n"


def _code(tmp_path: Path, body: str) -> bytes:
    (tmp_path / "data.s").write_text(DATA, encoding="utf-8")
    (tmp_path / "main.s").write_text(
        f'.import "data"\n.extern blob_start\n.alloc code at 0x008000 {{\n{body}}}\n', encoding="utf-8"
    )
    result = build_with_imports(
        tmp_path / "main.s",
        tmp_path / "out.sfc",
        module_paths=[tmp_path],
        output_dir=tmp_path / "obj",
        output_format="sfc",
    )
    assert result.exit_code == 0
    return (tmp_path / "out.sfc").read_bytes()[:3]


@pytest.mark.parametrize(("opcode", "byte"), [("mvn", 0x54), ("mvp", 0x44)])
def test_an_extern_source_bank_is_resolved_at_link(tmp_path: Path, opcode: str, byte: int) -> None:
    """Encoded `opcode, destbank, srcbank`."""
    assert _code(tmp_path, f"    {opcode} blob_start >> 16, 0x7E\n") == bytes([byte, 0x7E, 0x01])


def test_an_extern_destination_bank_is_resolved_at_link(tmp_path: Path) -> None:
    assert _code(tmp_path, "    mvn 0x7E, blob_start >> 16\n") == bytes([0x54, 0x01, 0x7E])


def test_both_operands_extern(tmp_path: Path) -> None:
    assert _code(tmp_path, "    mvn blob_start >> 16, (blob_start >> 16) + 1\n") == bytes([0x54, 0x02, 0x01])
