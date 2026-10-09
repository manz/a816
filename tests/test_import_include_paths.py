"""An imported module's `.include` searches the build's include paths.

Imported modules were parsed without them, so only the module's own
directory was searched: a file found under an include path from the
entry file was missing from a module (Feda).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.module_builder import build_with_imports
from a816.parse.ast.nodes import IncludeAstNode
from a816.parse.codegen.modules import _parse_import
from a816.program import Program


def test_an_imported_module_includes_from_the_include_paths(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "gen").mkdir()
    (tmp_path / "gen" / "data.s").write_text("VALUE = 0x42\n", encoding="utf-8")
    (tmp_path / "src" / "menu.s").write_text(
        '.include "data.s"\n.alloc menu at 0x008000 {\n    .db VALUE\n}\n', encoding="utf-8"
    )
    main = tmp_path / "src" / "main.s"
    main.write_text('.import "menu"\n', encoding="utf-8")

    result = build_with_imports(
        main,
        tmp_path / "out.ips",
        module_paths=[tmp_path / "src"],
        include_paths=[tmp_path / "gen"],
        output_dir=tmp_path / "obj",
    )

    assert result.exit_code == 0


def _included_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, parse_include_paths: list[Path] | None) -> str:
    """Where an import's parse locates `.include "src/sub/data.i"`, found only through `.`."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src" / "sub").mkdir(parents=True)
    (tmp_path / "src" / "sub" / "data.i").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "src" / "sub" / "m.s").write_text('.include "src/sub/data.i"\n', encoding="utf-8")
    program = Program()
    program.add_include_path(".")
    program.resolver.context.parse_include_paths = parse_include_paths
    parsed = _parse_import(Path("src/sub/m.s"), program.resolver)
    assert parsed is not None
    include = parsed.result.nodes[0]
    assert isinstance(include, IncludeAstNode)
    return str(include.resolved_path)


def test_an_import_names_an_included_file_as_the_module_build_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ff4 passes `.` as an include path: the module's own parse kept the file
    relative while the import's parse made it absolute, so the .adbg listed it twice."""
    assert _included_path(tmp_path, monkeypatch, [Path(".")]) == "src/sub/data.i"


def test_an_import_outside_a_build_searches_the_resolved_include_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _included_path(tmp_path, monkeypatch, None) == str(tmp_path.resolve() / "src" / "sub" / "data.i")
