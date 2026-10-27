"""Source strings compile without a file: `Program.compile_source` and
`ModuleBuilder.build(main_text=)`."""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.exceptions import AssemblyError
from a816.module_builder import ModuleBuilder
from a816.parse.nodes import NodeError
from a816.program import Program
from a816.writers import ObjectWriter


def test_compile_source_builds_an_object_in_memory() -> None:
    obj = Program().compile_source("*=0x038000\nentry:\n    rts\n")
    assert ("entry", 0x038000) in {(name, value) for name, value, _, _ in obj.symbols}


def test_compile_source_raises_on_a_parse_error() -> None:
    program = Program()
    with pytest.raises(AssemblyError):
        program.compile_source("*=0x038000\n.db (\n")


def test_compile_source_raises_located_codegen_errors() -> None:
    program = Program()
    with pytest.raises(NodeError):
        program.compile_source("*=0x038000\nlda.l missing\n")


def test_a_build_entry_can_be_a_string(tmp_path: Path) -> None:
    (tmp_path / "data.s").write_text("*=0x038000\nvalue:\n    .db 1\n")
    builder = ModuleBuilder(module_paths=[tmp_path], output_dir=tmp_path / "obj", use_cache=False)
    linked = builder.build(tmp_path / "main.s", main_text='.import "data"\n*=0x008000\n    lda.l value\n')
    assert ("value", 0x038000) in {(name, value) for name, value, _, _ in linked.symbols}


def test_an_uncached_build_writes_no_sidecar(tmp_path: Path) -> None:
    builder = ModuleBuilder(module_paths=[tmp_path], output_dir=tmp_path / "obj", use_cache=False)
    builder.build(tmp_path / "main.s", main_text="*=0x008000\n    rts\n")
    assert not list((tmp_path / "obj").glob("*.deps"))


def test_reading_the_object_leaves_the_writer_whole() -> None:
    writer = ObjectWriter()
    writer.begin()
    writer.start_section(0x8000)
    writer.write_block(b"", 0x8000)
    writer.object_file()
    writer.write_block(b"\x60", 0x8000)
    assert [section.code for section in writer.object_file().sections] == [b"\x60"]
