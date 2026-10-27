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

import pytest

from a816.context import AssemblyMode
from a816.parse.nodes import NodeError
from a816.program import Program
from tests import link_source


def _codegen(src: str) -> None:
    """Run `src`'s object-mode codegen; layout errors raise as `NodeError`."""
    program = Program()
    program.resolver.context.mode = AssemblyMode.OBJECT
    error, _ = program.parser.parse(src, "memory.s")
    assert error is None


def test_transitive_import_does_not_redefine_struct() -> None:
    """A common `inc` imported through two paths must load exactly once."""
    inc = """
.struct Point {
    word x
    word y
}
"""
    main = """
.import "a"
.import "b"
*=0x008000
    nop
"""
    linked = link_source(main, {"inc.s": inc, "a.s": '.import "inc"\n', "b.s": '.import "inc"\n'})
    assert linked.exit_code == 0


def test_struct_identical_redef_is_silent() -> None:
    """Two identical `.struct` declarations (via include) are a no-op."""
    src = """
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
"""
    # No NodeError raised, no failure to assemble.
    assert link_source(src).exit_code == 0


def test_struct_mismatched_redef_still_raises() -> None:
    """A redefinition that *changes* the layout is still an error."""
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
        _codegen(src)


def test_symbol_rebind_with_same_value_is_silent(caplog: pytest.LogCaptureFixture) -> None:
    src = """
foo = 0x42
foo = 0x42
"""
    with caplog.at_level(logging.WARNING):
        assert link_source(src).exit_code == 0
    assert not any("already defined" in rec.message for rec in caplog.records), "no-op rebind must not log"


def test_symbol_rebind_is_silent_upsert(caplog: pytest.LogCaptureFixture) -> None:
    """Multi-pass resolution legitimately rewrites label addresses across
    passes; the symbol-already-defined warning was producing thousands of
    false positives on real builds. `add_symbol` is now a silent upsert —
    real duplicate-declaration errors surface from the parser / codegen
    layers (struct redef, etc.), not from this hot path."""
    src = """
foo = 0x42
foo = 0x43
"""
    with caplog.at_level(logging.WARNING):
        assert link_source(src).exit_code == 0
    assert not any("already defined" in rec.message for rec in caplog.records)
