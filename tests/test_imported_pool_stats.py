"""`POOL.capacity` / `.fragments` / `.largest_chunk` read the same through `.import`.

A local `.pool` published its stats; one that came from an imported module
did not, so `lda.w #engine_state.capacity` was E0200 in the importer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.module_builder import ModuleBuilder

MAPS = (
    ".map identifier=3 bank_range=0x7e, 0x7f addr_range=0x0000, 0xffff mask=0x10000 writable=1\n"
    ".map identifier=1 bank_range=0x00, 0x3f addr_range=0x8000, 0xffff mask=0x8000\n"
)
POOL = ".pool engine_state { bss range 0x7e0040 0x7e007f strategy order }\n"


def _code(root: Path, stat: str, imported: bool) -> bytes:
    use = f".alloc main_code at 0x008000 {{\n    lda.w #engine_state.{stat}\n    rts\n}}\n"
    if imported:
        (root / "estate.s").write_text(MAPS + POOL, encoding="utf-8")
        source = '.import "estate"\n' + use
    else:
        source = MAPS + POOL + use
    main = root / "main.s"
    main.write_text(source, encoding="utf-8")
    obj = ModuleBuilder(module_paths=[root], include_paths=[root], output_dir=root / "obj").build(main)
    return next(section.code for section in obj.sections if section.placed_base == 0x008000)


def test_imported_capacity_reads_the_pool_size(tmp_path: Path) -> None:
    assert _code(tmp_path, "capacity", imported=True) == b"\xa9\x40\x00\x60"


@pytest.mark.parametrize("stat", ["capacity", "fragments", "largest_chunk"])
def test_imported_stats_match_local_ones(tmp_path: Path, stat: str) -> None:
    (tmp_path / "local").mkdir()
    (tmp_path / "imported").mkdir()
    local = _code(tmp_path / "local", stat, imported=False)

    assert _code(tmp_path / "imported", stat, imported=True) == local
