"""`.import` is rejected where it would ride a placement context.

Modules own their placement (`.alloc at` / `.alloc in POOL`). An
`.import` after `*=` or inside an `.alloc` body used to be accepted
and silently placed the module somewhere else; it is now a located
hard error pointing at the `.import` keyword.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from a816.context import AssemblyMode
from a816.error_codes import E_CODEGEN_IMPORT_IN_PLACEMENT
from a816.parse.nodes import NodeError
from a816.program import Program
from tests import link_source


def _codegen(src: str) -> Program:
    """Program whose resolver ran `src`'s object-mode codegen: placement
    errors raise as `NodeError`."""
    program = Program()
    program.resolver.context.mode = AssemblyMode.OBJECT
    error, _ = program.parser.parse(src, "main.s")
    assert error is None
    return program


def _assemble(src: str, files: dict[str, str] | None = None) -> None:
    linked = link_source(src, files)
    assert linked.exit_code == 0


def _assemble_error(src: str) -> NodeError:
    with pytest.raises(NodeError) as excinfo:
        _codegen(src)
    return excinfo.value


def test_import_after_star_eq_is_rejected() -> None:
    error = _assemble_error('*=0x008000\n.import "@std/snes/ppu"\n')
    assert error.code == str(E_CODEGEN_IMPORT_IN_PLACEMENT)


def test_import_after_star_eq_message_points_at_the_prelude() -> None:
    error = _assemble_error('*=0x008000\n.import "@std/snes/ppu"\n')
    assert "modules own their placement" in error.message


def test_import_after_star_eq_caret_sits_on_the_import_keyword() -> None:
    error = _assemble_error('*=0x008000\n.import "@std/snes/ppu"\n')
    token = error.file_info
    assert token is not None
    assert token.position is not None
    assert (token.value, token.position.line) == ("import", 1)


def test_import_inside_alloc_body_is_rejected() -> None:
    error = _assemble_error('.alloc at 0x008000 {\n    .import "@std/snes/ppu"\n}\n')
    assert error.code == str(E_CODEGEN_IMPORT_IN_PLACEMENT)


def test_import_inside_pooled_alloc_body_is_rejected() -> None:
    src = '.pool p { range 0x008000 0x00ffff }\n.alloc blob in p {\n    .import "@std/snes/ppu"\n}\n'
    error = _assemble_error(src)
    assert error.code == str(E_CODEGEN_IMPORT_IN_PLACEMENT)


def test_import_inside_relocate_body_is_rejected() -> None:
    src = (
        ".pool p { range 0x009000 0x009fff }\n"
        "old_routine = 0x008000\n"
        '.relocate old_routine 0x008000 0x008010 into p {\n    .import "@std/snes/ppu"\n}\n'
    )
    error = _assemble_error(src)
    assert error.code == str(E_CODEGEN_IMPORT_IN_PLACEMENT)


def test_import_after_star_eq_inside_scope_is_rejected() -> None:
    error = _assemble_error('*=0x008000\n.scope s {\n    .import "@std/snes/ppu"\n}\n')
    assert error.code == str(E_CODEGEN_IMPORT_IN_PLACEMENT)


def test_import_in_prelude_before_star_eq_is_accepted() -> None:
    _assemble('.import "@std/snes/ppu"\n*=0x008000\n    lda.w PPU_BASE\n')


def test_import_between_alloc_blocks_is_not_a_hard_error() -> None:
    """Misplaced but not riding a placement context: ST002 lints it."""
    src = '.alloc at 0x008000 {\n    nop\n}\n.import "@std/snes/ppu"\n.alloc at 0x009000 {\n    nop\n}\n'
    _assemble(src)


def test_star_eq_inside_imported_module_does_not_leak_into_importer() -> None:
    legacy = '"""Legacy."""\n*=0x008000\nlegacy_entry:\n    rts\n'
    _assemble('.import "legacy"\n.import "@std/snes/ppu"\n', {"legacy.s": legacy})


def test_import_after_star_eq_is_rejected_in_object_mode(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    main = tmp_path / "main.s"
    main.write_text('*=0x008000\n.import "@std/snes/ppu"\n')
    with caplog.at_level(logging.ERROR):
        rc = Program().assemble_as_object(str(main), tmp_path / "main.o")
    assert (rc, str(E_CODEGEN_IMPORT_IN_PLACEMENT) in caplog.text) == (-1, True)


def test_alloc_depth_unwinds_after_a_rejected_import() -> None:
    program = Program()
    program.resolver.context.mode = AssemblyMode.OBJECT
    with pytest.raises(NodeError):
        program.parser.parse('.alloc at 0x008000 {\n    .import "@std/snes/ppu"\n}\n', "main.s")
    assert program.resolver.placement_body_depth == 0


def test_star_eq_cursor_does_not_leak_into_the_next_source_unit() -> None:
    program = _codegen("*=0x008000\n    nop\n")
    error, _ = program.parser.parse('.import "@std/snes/ppu"\n', "second.s")
    assert error is None
