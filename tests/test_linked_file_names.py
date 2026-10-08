"""The linked file table names each source once, relative to the working directory.

Objects name a file the way their parse reached it: `src/libmz.i` from one
module, `/abs/.../src/libmz.i` from another that imported its includer by
absolute path (ff4). The `.adbg` listed both, and absolute names made it
differ between checkouts (Bahamut Lagoon's worktrees).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.linker import Linker
from a816.object_file import ObjectFile


def _linked_files(*tables: list[str]) -> list[str]:
    linker = Linker([ObjectFile([], [], files=list(table)) for table in tables])
    for obj in linker.object_files:
        linker._merge_file_table(obj)
    return linker.linked_files


def test_one_file_reached_two_ways_is_one_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    assert _linked_files(["src/libmz.i"], [str(tmp_path / "src" / "libmz.i")]) == ["src/libmz.i"]


def test_a_file_under_the_working_directory_is_named_relative(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    assert _linked_files([str(tmp_path / "src" / "bl.s")]) == ["src/bl.s"]


def test_a_file_outside_it_stays_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "project").mkdir()
    monkeypatch.chdir(tmp_path / "project")

    assert _linked_files([str(tmp_path / "lib" / "std.i")]) == [str((tmp_path / "lib" / "std.i").resolve())]


def test_a_placeholder_name_is_kept() -> None:
    assert _linked_files(["<linked>"]) == ["<linked>"]


def test_line_entries_point_at_the_merged_entry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    linker = Linker([ObjectFile([], [], files=["a.s", str(tmp_path / "a.s")])])

    assert linker._merge_file_table(linker.object_files[0]) == {0: 0, 1: 0}
