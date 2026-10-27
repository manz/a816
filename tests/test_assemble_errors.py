"""`assemble_as_object` always returns; never calls sys.exit.

Embedders (LSP, fluff, tests) drive the assembler programmatically and
would otherwise have to catch `SystemExit`. A failed compile returns -1
and logs the located diagnostic; `AssemblyError` and `NodeError` share
`A816Error` so a single `except` catches both parser and codegen failures.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from a816.exceptions import A816Error, AssemblyError
from a816.parse.nodes import NodeError
from a816.program import Program


def _compile(src: str) -> int:
    tmp = Path(tempfile.mkdtemp())
    asm = tmp / "main.s"
    asm.write_text(src, encoding="utf-8")
    return Program().assemble_as_object(str(asm), tmp / "main.o")


def test_assemble_as_object_returns_minus_one_on_parser_error(caplog: pytest.LogCaptureFixture) -> None:
    rc = _compile(".struct\n")  # parser error: missing name
    assert rc == -1
    assert "expected identifier" in caplog.text


def test_assemble_as_object_returns_minus_one_on_codegen_error(caplog: pytest.LogCaptureFixture) -> None:
    src = """
        .struct Bad {
            qword x
        }
    """
    rc = _compile(src)
    assert rc == -1
    assert "Unknown struct field type 'qword'" in caplog.text


def test_assemble_as_object_returns_minus_one_on_addressing_mode_error(caplog: pytest.LogCaptureFixture) -> None:
    rc = _compile("*=0x008000\nnop #0x00\n")
    assert rc == -1
    assert "Addressing mode (immediate)" in caplog.text


def test_assembly_error_and_node_error_share_a816_base() -> None:
    """A single `except A816Error` catches both shapes for embedders."""
    assert issubclass(AssemblyError, A816Error)
    assert issubclass(NodeError, A816Error)


def test_assemble_as_object_returns_zero_on_success() -> None:
    src = """
        *=0x008000
            nop
    """
    assert _compile(src) == 0
