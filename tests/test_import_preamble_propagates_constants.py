"""`.import "preamble"` should make the preamble's `:=` constants
visible to the importer at compile time — the plan PR79 calls out the
preamble pattern as the prelude replacement:

> Users add `.import "preamble"` at the top of main; the preamble
> module is just a `.s` file with shared structs, constants, pool
> decls, typed binds.

`.if FLAG { ... }` inside an `.import`er needs FLAG bound by codegen
time of the `.if` so the branch evaluates. The current import
auto-extern classifier inlines compile-time material (structs, macros,
constants) — including `:=` SymbolAffectationAstNode — so the
constant should land in the importer's scope. The test below pins
that contract.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from a816.program import Program

_PREAMBLE = """
ENABLE_INTRO := 1
DEBUG := 0
"""


def test_import_preamble_makes_constants_visible_for_if() -> None:
    """Module `.import`s a preamble and uses `.if ENABLE_INTRO` to
    gate an alloc body. Object compile should see ENABLE_INTRO == 1
    and emit the conditional content. If the constant doesn't
    propagate, the `.if` silently evaluates False and the test
    sentinel (`.db 0xEE`) is missing from the section."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "preamble.s").write_text(_PREAMBLE, encoding="utf-8")
        (tmp / "mod.s").write_text(
            '.import "preamble"\n'
            ".alloc at 0x208000 {\n"
            "    .if ENABLE_INTRO {\n"
            "        .db 0xEE\n"
            "    } else {\n"
            "        .db 0x00\n"
            "    }\n"
            "}\n",
            encoding="utf-8",
        )
        program = Program()
        program.add_module_path(tmp)
        obj_path = tmp / "mod.o"
        assert program.assemble_as_object(str(tmp / "mod.s"), obj_path) == 0
        # Read the .o and check the alloc body section contains 0xEE.
        from a816.object_file import ObjectFile

        obj = ObjectFile.from_file(str(obj_path))
        sections = obj.sections
        assert sections, "module emitted no sections"
        # The pinned-at-0x208000 section should hold one byte: 0xEE.
        body = next((s for s in sections if s.code), None)
        assert body is not None, "no non-empty section"
        assert body.code == b"\xee", (
            f"expected 0xEE from `.if ENABLE_INTRO` true branch, got {body.code!r} — "
            "preamble's `:=` constant probably didn't propagate"
        )
