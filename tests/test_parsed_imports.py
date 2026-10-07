"""The parser records `.import`s as it meets them, so module discovery need not walk the AST."""

from __future__ import annotations

from pathlib import Path

from a816.parse.ast.nodes import ImportAstNode
from a816.parse.ast.visitor import walk
from a816.parse.mzparser import A816Parser
from a816.parse.parser_states.directives import clear_include_ast_cache


def _parse(root: Path, source: str) -> list[str] | None:
    main = root / "main.s"
    main.write_text(source, encoding="utf-8")
    return A816Parser.parse_as_ast(source, str(main), include_paths=[root]).imports


def test_imports_come_in_source_order(tmp_path: Path) -> None:
    assert _parse(tmp_path, '.import "a"\n.import "b"\n') == ["a", "b"]


def test_imports_nested_in_blocks_are_recorded(tmp_path: Path) -> None:
    source = '.scope s {\n    .import "inner"\n}\n.if 1 {\n    .import "then"\n} .else {\n    .import "else"\n}\n'

    assert _parse(tmp_path, source) == ["inner", "then", "else"]


def test_an_included_file_contributes_its_imports_at_the_include(tmp_path: Path) -> None:
    clear_include_ast_cache()
    (tmp_path / "deps.i").write_text('.import "from_include"\n', encoding="utf-8")

    assert _parse(tmp_path, '.import "first"\n.include "deps.i"\n.import "last"\n') == [
        "first",
        "from_include",
        "last",
    ]


def test_a_cached_include_still_contributes_its_imports(tmp_path: Path) -> None:
    clear_include_ast_cache()
    (tmp_path / "deps.i").write_text('.import "from_include"\n', encoding="utf-8")
    _parse(tmp_path, '.include "deps.i"\n')

    assert _parse(tmp_path, '.include "deps.i"\n.include "deps.i"\n') == ["from_include", "from_include"]


def test_recorded_imports_match_a_walk(tmp_path: Path) -> None:
    clear_include_ast_cache()
    (tmp_path / "deps.i").write_text('.import "c"\n', encoding="utf-8")
    source = '.import "a"\n.scope s {\n    .include "deps.i"\n}\n.import "b"\n'
    result = A816Parser.parse_as_ast(source, str(tmp_path / "main.s"), include_paths=[tmp_path])

    walked = [node.module_name for node in walk(result.nodes) if isinstance(node, ImportAstNode)]

    assert result.imports == walked


def test_a_parse_with_errors_records_nothing(tmp_path: Path) -> None:
    assert _parse(tmp_path, '.import "a"\nlda #\n') is None
