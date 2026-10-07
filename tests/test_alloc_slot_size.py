"""A pooled alloc must emit exactly the slot it reserved; a drift is E0337, not bytes spilled into a neighbour."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from a816.parse.nodes import NodeError
from a816.parse.nodes.alloc import AllocNode
from a816.program import Program
from tests import StubWriter
from tests.test_reserve import _PREAMBLE

_SRC = _PREAMBLE + ".alloc body in code {\n    nop\n    nop\n}\n"


@pytest.fixture
def undercounting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the pass-1 measure one byte short of what the body emits."""
    measure = AllocNode._measure_body
    monkeypatch.setattr(AllocNode, "_measure_body", lambda self: measure(self) - 1)


def test_an_object_build_rejects_a_body_longer_than_its_slot(undercounting: None) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "m.s"
        source.write_text(_SRC, encoding="utf-8")
        program = Program()
        assert program.assemble_as_object(str(source), Path(tmp) / "m.o") != 0


def test_a_direct_build_rejects_a_body_longer_than_its_slot(undercounting: None) -> None:
    program, writer = Program(), StubWriter()
    with pytest.raises(NodeError, match=r"alloc 'body' emitted 2 bytes into a 1-byte slot"):
        program.assemble_string_with_emitter(_SRC, "m.s", writer)


def test_a_body_that_fills_its_slot_builds() -> None:
    writer = StubWriter()
    Program().assemble_string_with_emitter(_SRC, "m.s", writer)
    assert b"".join(writer.data) == b"\xea\xea"
