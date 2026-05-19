"""`assemble_with_emitter` always returns; never calls sys.exit.

Embedders (LSP, fluff, tests) drive the assembler programmatically and
would otherwise have to catch `SystemExit`. The embedder-facing
`assemble_string_with_emitter` raises `A816Error` (parent of both
`AssemblyError` and `NodeError`) so a single `except` catches both
parser and codegen failures.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from a816.exceptions import A816Error, AssemblyError
from a816.parse.nodes import NodeError
from a816.program import Program
from a816.writers import SFCWriter
from tests import StubWriter


def _write_source(src: str) -> tuple[Path, Path]:
    tmp = Path(tempfile.mkdtemp())
    asm = tmp / "main.s"
    asm.write_text(src, encoding="utf-8")
    return tmp, asm


def test_assemble_with_emitter_returns_128_on_parser_error() -> None:
    tmp, asm = _write_source(".struct\n")  # parser error: missing name
    out = tmp / "out.sfc"
    program = Program()
    with out.open("wb") as fh:
        rc = program.assemble_with_emitter(str(asm), SFCWriter(fh))
    assert rc == 128


def test_assemble_with_emitter_returns_128_on_codegen_error() -> None:
    src = """
        .struct Bad {
            qword x
        }
    """
    tmp, asm = _write_source(src)
    out = tmp / "out.sfc"
    program = Program()
    with out.open("wb") as fh:
        rc = program.assemble_with_emitter(str(asm), SFCWriter(fh))
    assert rc == 128


def test_assemble_string_raises_assembly_error_on_parser_failure() -> None:
    program = Program()
    with pytest.raises(AssemblyError):
        program.assemble_string_with_emitter(".struct\n", "x.s", StubWriter())


def test_assemble_string_raises_node_error_on_codegen_failure() -> None:
    program = Program()
    with pytest.raises(NodeError):
        program.assemble_string_with_emitter("nop #0x00", "x.s", StubWriter())


def test_assembly_error_and_node_error_share_a816_base() -> None:
    """A single `except A816Error` catches both shapes for embedders."""
    program = Program()
    with pytest.raises(A816Error):
        program.assemble_string_with_emitter(".struct\n", "x.s", StubWriter())
    with pytest.raises(A816Error):
        program.assemble_string_with_emitter("nop #0x00", "x.s", StubWriter())


def test_assemble_with_emitter_returns_zero_on_success() -> None:
    src = """
        *=0x008000
            nop
    """
    tmp, asm = _write_source(src)
    out = tmp / "out.sfc"
    program = Program()
    with out.open("wb") as fh:
        rc = program.assemble_with_emitter(str(asm), SFCWriter(fh))
    assert rc == 0
