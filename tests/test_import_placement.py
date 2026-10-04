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


class _NoopEmitter:
    def begin(self) -> None: ...

    def end(self) -> None: ...

    def write_block_header(self, *_: object, **__: object) -> None: ...

    def write_block(self, *_: object, **__: object) -> None: ...


def _assemble(src: str, module_dir: Path | None = None) -> None:
    program = Program()
    program.resolver.context.mode = AssemblyMode.DIRECT
    if module_dir is not None:
        program.add_module_path(str(module_dir))
    program.assemble_string_with_emitter(src, "main.s", _NoopEmitter())


def _assemble_error(src: str) -> NodeError:
    with pytest.raises(NodeError) as excinfo:
        _assemble(src)
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


def test_star_eq_inside_imported_module_does_not_leak_into_importer(tmp_path: Path) -> None:
    (tmp_path / "legacy.s").write_text('"""Legacy."""\n*=0x008000\nlegacy_entry:\n    rts\n')
    _assemble('.import "legacy"\n.import "@std/snes/ppu"\n', tmp_path)


def test_import_after_star_eq_is_rejected_in_object_mode(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    main = tmp_path / "main.s"
    main.write_text('*=0x008000\n.import "@std/snes/ppu"\n')
    with caplog.at_level(logging.ERROR):
        rc = Program().assemble_as_object(str(main), tmp_path / "main.o")
    assert (rc, str(E_CODEGEN_IMPORT_IN_PLACEMENT) in caplog.text) == (-1, True)


def test_alloc_depth_unwinds_after_a_rejected_import() -> None:
    program = Program()
    program.resolver.context.mode = AssemblyMode.DIRECT
    emitter = _NoopEmitter()
    with pytest.raises(NodeError):
        program.assemble_string_with_emitter(
            '.alloc at 0x008000 {\n    .import "@std/snes/ppu"\n}\n', "main.s", emitter
        )
    assert program.resolver.placement_body_depth == 0


def test_star_eq_cursor_does_not_leak_into_the_next_source_unit() -> None:
    program = Program()
    program.resolver.context.mode = AssemblyMode.DIRECT
    program.assemble_string_with_emitter("*=0x008000\n    nop\n", "first.s", _NoopEmitter())
    program.assemble_string_with_emitter('.import "@std/snes/ppu"\n', "second.s", _NoopEmitter())
