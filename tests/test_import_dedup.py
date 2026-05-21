"""`.import` cascade dedup + struct idempotent redefinition.

Two regressions from the typed-struct PR (#60):

1. Transitive `.import` cascades re-parse the same `.s` source through
   different paths, blowing up on the struct-redef check.
2. `Symbol already defined` warnings fire on every multi-pass re-bind
   even when the value is unchanged — kilometres of log noise on real
   projects.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pytest

from a816.parse.nodes import NodeError
from a816.program import Program


def _write(root: Path, name: str, body: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_transitive_import_does_not_redefine_struct() -> None:
    """A common `inc` imported through two paths must load exactly once."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(
            root,
            "inc.s",
            """
.struct Point {
    word x
    word y
}
""",
        )
        _write(root, "a.s", '.import "inc"\n')
        _write(root, "b.s", '.import "inc"\n')
        main = _write(
            root,
            "main.s",
            """
.import "a"
.import "b"
*=0x008000
    nop
""",
        )

        program = Program()
        program.add_module_path(root)
        program.add_include_path(root)
        out = root / "out.ips"
        assert program.assemble_as_patch(str(main), out) == 0


def test_struct_identical_redef_is_silent() -> None:
    """Two identical `.struct` declarations (via include) are a no-op."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write(
            root,
            "main.s",
            """
.struct Point {
    word x
    word y
}
.struct Point {
    word x
    word y
}
*=0x008000
    nop
""",
        )
        program = Program()
        out = root / "out.ips"
        # No NodeError raised, no failure to assemble.
        assert program.assemble_as_patch(str(root / "main.s"), out) == 0


def test_struct_mismatched_redef_still_raises() -> None:
    """A redefinition that *changes* the layout is still an error."""
    program = Program()
    src = """
.struct Point {
    word x
    word y
}
.struct Point {
    word x
    word z
}
"""
    with pytest.raises(NodeError, match="different field layout"):
        program.assemble_string_with_emitter(src, "memory.s", _NoopEmitter())


def test_symbol_rebind_with_same_value_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    program = Program()
    src = """
foo = 0x42
foo = 0x42
"""
    with caplog.at_level(logging.WARNING):
        program.assemble_string_with_emitter(src, "memory.s", _NoopEmitter())
    assert not any("already defined" in rec.message for rec in caplog.records), "no-op rebind must not log"


def test_symbol_rebind_is_silent_upsert(caplog: pytest.LogCaptureFixture) -> None:
    """Multi-pass resolution legitimately rewrites label addresses across
    passes; the symbol-already-defined warning was producing thousands of
    false positives on real builds. `add_symbol` is now a silent upsert —
    real duplicate-declaration errors surface from the parser / codegen
    layers (struct redef, etc.), not from this hot path."""
    program = Program()
    src = """
foo = 0x42
foo = 0x43
"""
    with caplog.at_level(logging.WARNING):
        program.assemble_string_with_emitter(src, "memory.s", _NoopEmitter())
    assert not any("already defined" in rec.message for rec in caplog.records)


class _NoopEmitter:
    def begin(self) -> None: ...

    def end(self) -> None: ...

    def write_block_header(self, *_: object, **__: object) -> None: ...

    def write_block(self, *_: object, **__: object) -> None: ...
