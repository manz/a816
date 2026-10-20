"""`E0400` names both definitions of a duplicate global.

ff4 on a42: `MENU_HDMA_TABLE_SIZE` defined as 40 in one module and 0x40 in
another failed the link with the symbol name only; finding the second
definition took a grep.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.exceptions import DuplicateSymbolError, PinnedTwiceLinkError
from a816.linker import Linker
from a816.object_file import ObjectFile
from a816.program import Program


def _object(tmp_path: Path, name: str, source: str) -> ObjectFile:
    (tmp_path / f"{name}.s").write_text(source)
    assert Program().assemble_as_object(str(tmp_path / f"{name}.s"), tmp_path / f"{name}.o") == 0
    return ObjectFile.from_file(str(tmp_path / f"{name}.o"))


def test_a_duplicate_global_names_both_modules_and_values(tmp_path: Path) -> None:
    first = _object(tmp_path, "inventory_rolling", "MENU_HDMA_TABLE_SIZE = 40\n")
    second = _object(tmp_path, "wram_layout", "MENU_HDMA_TABLE_SIZE = 0x40\n")
    linker = Linker([first, second])
    with pytest.raises(DuplicateSymbolError) as excinfo:
        linker.link(base_address=0x8000)
    formatted = excinfo.value.format()
    assert "E0400" in formatted
    assert f"{tmp_path / 'inventory_rolling.o'} = 0x28" in formatted
    assert f"{tmp_path / 'wram_layout.o'} = 0x40" in formatted


def test_an_object_with_source_lines_is_named_by_its_source(tmp_path: Path) -> None:
    obj = _object(tmp_path, "code", "*=0x008000\nentry:\n    rts\n")
    assert obj.describe().endswith("code.s")


def test_two_files_pinning_one_address_on_one_line_overlap(tmp_path: Path) -> None:
    """ff6 assassin17: anonymous allocs are named from address and line, so
    two patches pinning the same address on the same line clashed by name."""
    source = '"""P."""\n.alloc at 0x008000 {\n    .db 1\n}\n'
    linker = Linker([_object(tmp_path, "command_list_g", source), _object(tmp_path, "command_list_w", source)])
    with pytest.raises(PinnedTwiceLinkError) as excinfo:
        linker.link(base_address=0x8000)
    formatted = excinfo.value.format()

    assert "E0408" in formatted
    assert "$00:8000" in formatted


def test_two_allocs_sharing_a_name_still_clash_by_name(tmp_path: Path) -> None:
    pool = ".pool rom { range 0x008000 0x00ffff  strategy order }\n"
    first = _object(tmp_path, "a", f'"""A."""\n{pool}.alloc f in rom {{\n    .db 1\n}}\n')
    second = _object(tmp_path, "b", f'"""B."""\n{pool}\n.alloc f in rom {{\n    .db 2\n}}\n')
    linker = Linker([first, second])
    with pytest.raises(DuplicateSymbolError, match="duplicate symbol 'f'"):
        linker.link(base_address=0x8000)
