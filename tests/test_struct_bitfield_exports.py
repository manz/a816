"""Bit-field `.mask` / `.shift` publish under their struct only.

`.struct T { u3 lo ... }` used to export `lo.mask` / `lo.shift` bare as well
as `T.lo.mask`, so two modules with a same-named bit field (the stdlib's
`unused`) clashed as duplicate globals at link.
"""

from __future__ import annotations

from pathlib import Path

from a816.object_file import ObjectFile
from a816.program import Program
from tests import BANK_40_MAP, build_rom


def test_bit_field_masks_export_only_under_their_struct(tmp_path: Path) -> None:
    (tmp_path / "m.s").write_text(".struct T {\n    u3 lo\n    u5 hi\n}\n")
    assert Program().assemble_as_object(str(tmp_path / "m.s"), tmp_path / "m.o") == 0
    names = {name for name, *_ in ObjectFile.from_file(str(tmp_path / "m.o")).symbols}
    assert {"T.lo.mask", "T.lo.shift", "T.hi.mask", "T.hi.shift"} <= names
    assert not {"lo.mask", "lo.shift", "hi.mask", "hi.shift"} & names


def test_two_modules_with_same_named_bit_fields_link(tmp_path: Path) -> None:
    files = {
        "main.s": BANK_40_MAP + '.import "a"\n.import "b"\n.alloc c at 0x400000 {\n    lda.b #B.unused.mask\n}\n',
        "a.s": ".struct A {\n    u3 unused\n    u5 x\n}\n",
        "b.s": ".struct B {\n    u1 x\n    u7 unused\n}\n",
    }
    rc, rom = build_rom(tmp_path, files)
    assert (rc, rom[:2]) == (0, b"\xa9\xfe")
