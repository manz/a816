"""Data directives and `.istruct` bytes record where they come from.

An alloc holding only `.db` / `.dw` used to carry no line entry in its
`.o`, so the `.adbg` and link-time diagnostics had nothing to point at.
"""

from __future__ import annotations

from pathlib import Path

from a816.debug_info import read
from a816.module_builder import build_with_imports
from a816.object_file import ObjectFile
from a816.program import Program

POOL = ".pool rom { range 0x008000 0x00ffff }\n"


def _section_lines(tmp_path: Path, source: str) -> list[tuple[int, int]]:
    """(offset, 0-based line) of the first section's line entries."""
    src = tmp_path / "main.s"
    src.write_text(source, encoding="utf-8")
    obj = tmp_path / "main.o"
    Program().assemble_as_object(str(src), obj)
    section = ObjectFile.from_file(str(obj)).sections[0]
    return [(offset, line) for offset, _file, line, _col, _flags in section.lines]


def test_data_only_alloc_records_one_entry_per_directive(tmp_path: Path) -> None:
    source = POOL + ".alloc table in rom {\n    .db 1, 2, 3\n    .dw 0x1234, 0x5678\n}\n"

    assert _section_lines(tmp_path, source) == [(0, 2), (3, 3)]


def test_istruct_records_its_fields_and_fill(tmp_path: Path) -> None:
    source = (
        POOL + ".struct S {\n    byte a\n    byte[3] name\n}\n"
        ".alloc table in rom {\n    .istruct S {\n        a = 1\n    }\n}\n"
    )

    assert _section_lines(tmp_path, source) == [(0, 7), (1, 6)]


def test_patch_string_records_its_field(tmp_path: Path) -> None:
    source = '.struct H {\n    byte[2] title\n    byte maker\n}\n.patch H at 0x008000 {\n    title = "AB"\n}\n'

    assert _section_lines(tmp_path, source) == [(0, 5)]


def test_debug_info_names_data_lines(tmp_path: Path) -> None:
    src = tmp_path / "main.s"
    src.write_text("*=0x008000\n.db 1, 2\nnop\n.dw 3\n", encoding="utf-8")
    result = build_with_imports(
        src, tmp_path / "out.ips", output_dir=tmp_path / "obj", use_a816_toml=False, use_cache=False
    )
    assert result.debug_info_path is not None
    info = read(result.debug_info_path)

    assert [(e.address, e.line) for e in info.lines] == [(0x008000, 1), (0x008002, 2), (0x008003, 3)]
