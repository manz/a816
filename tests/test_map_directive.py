"""`.map` directive parsing and propagation."""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.module_builder import BuildResult, build_with_imports
from a816.parse.ast.nodes import AssignAstNode, MapAstNode
from a816.parse.mzparser import A816Parser
from a816.program import Program

_MAP_LINE = ".map identifier=1 bank_range=0x00, 0x3f addr_range=0x8000, 0xffff mask=0x8000\n"


def test_map_stops_at_end_of_line_before_assign() -> None:
    result = A816Parser.parse_as_ast(_MAP_LINE + "ppu := 0x2100\n")
    assert result.error is None


def test_map_keeps_its_attributes_when_next_line_is_identifier() -> None:
    result = A816Parser.parse_as_ast(_MAP_LINE + "ppu := 0x2100\n")
    map_node = result.nodes[0]
    assert isinstance(map_node, MapAstNode) and map_node.args["mask"] == 0x8000


def test_map_next_line_assign_is_its_own_node() -> None:
    result = A816Parser.parse_as_ast(_MAP_LINE + "\nppu := 0x2100\n")
    assert isinstance(result.nodes[1], AssignAstNode)


def test_map_still_rejects_unknown_attribute_on_same_line() -> None:
    result = A816Parser.parse_as_ast(".map identifier=1 bogus=2\n")
    assert result.error is not None and "bogus" in result.error


_PREAMBLE = (
    ".map identifier=1 bank_range=0x00, 0x3f addr_range=0x8000, 0xffff mask=0x8000 mirror_bank_range=0x80, 0xbf\n"
    ".map identifier=2 bank_range=0x7e, 0x7f addr_range=0x0000, 0xffff mask=0x10000 writable=1\n"
    ".map identifier=3 bank_range=0x70, 0x70 addr_range=0x0000, 0x7fff mask=0x8000 writable=1\n"
)
_SRAM_MAP_CONFLICT = ".map identifier=3 bank_range=0x71, 0x71 addr_range=0x0000, 0x7fff mask=0x8000 writable=1\n"
_SRAM_ALLOC = '.import "preamble"\n.alloc at 0x708000 {\n.db 1\n}\n'
_MAIN = '.import "preamble"\n.import "sram"\n*=0x008000\n    rts\n'


def _build(root: Path, sram: str) -> BuildResult:
    (root / "preamble.s").write_text(_PREAMBLE, encoding="utf-8")
    (root / "sram.s").write_text(sram, encoding="utf-8")
    (root / "main.s").write_text(_MAIN, encoding="utf-8")
    return build_with_imports(
        main_source=root / "main.s",
        output_file=root / "out.ips",
        module_paths=[root],
        output_dir=root / "obj",
    )


def test_import_carries_map_into_importer(tmp_path: Path) -> None:
    result = _build(tmp_path, _SRAM_ALLOC)
    assert result.exit_code == 0, result.diagnostics


def test_import_accepts_identical_map_in_importer(tmp_path: Path) -> None:
    result = _build(tmp_path, _PREAMBLE.splitlines(keepends=True)[2] + _SRAM_ALLOC)
    assert result.exit_code == 0, result.diagnostics


def test_import_rejects_conflicting_map_in_importer(tmp_path: Path) -> None:
    result = _build(tmp_path, _SRAM_MAP_CONFLICT + _SRAM_ALLOC)
    assert result.exit_code != 0


def test_conflicting_map_diagnostic_names_identifier(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _build(tmp_path, _SRAM_MAP_CONFLICT + _SRAM_ALLOC)
    assert "error[E0308]: conflicting `.map '3'`" in caplog.text


def test_conflicting_map_diagnostic_points_at_import_site(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _build(tmp_path, _SRAM_MAP_CONFLICT + _SRAM_ALLOC)
    assert "sram.s:2:9" in caplog.text


def test_source_only_import_conflict_points_at_imported_map(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    (tmp_path / "preamble.s").write_text(_PREAMBLE, encoding="utf-8")
    (tmp_path / "sram.s").write_text(_SRAM_MAP_CONFLICT + _SRAM_ALLOC, encoding="utf-8")
    program = Program()
    program.resolver.context.module_paths = [tmp_path]
    program.assemble_as_object(str(tmp_path / "sram.s"), tmp_path / "sram.o")
    assert "preamble.s:3:" in caplog.text


def test_object_only_import_replays_map(tmp_path: Path) -> None:
    (tmp_path / "preamble.s").write_text(_PREAMBLE, encoding="utf-8")
    assert Program().assemble_as_object(str(tmp_path / "preamble.s"), tmp_path / "preamble.o") == 0
    (tmp_path / "preamble.s").unlink()
    (tmp_path / "sram.s").write_text(_SRAM_ALLOC, encoding="utf-8")
    program = Program()
    program.resolver.context.module_paths = [tmp_path]
    assert program.assemble_as_object(str(tmp_path / "sram.s"), tmp_path / "sram.o") == 0


def test_object_only_import_rejects_conflicting_map(tmp_path: Path) -> None:
    (tmp_path / "preamble.s").write_text(_PREAMBLE, encoding="utf-8")
    assert Program().assemble_as_object(str(tmp_path / "preamble.s"), tmp_path / "preamble.o") == 0
    (tmp_path / "preamble.s").unlink()
    (tmp_path / "sram.s").write_text(_SRAM_MAP_CONFLICT + _SRAM_ALLOC, encoding="utf-8")
    program = Program()
    program.resolver.context.module_paths = [tmp_path]
    assert program.assemble_as_object(str(tmp_path / "sram.s"), tmp_path / "sram.o") != 0
