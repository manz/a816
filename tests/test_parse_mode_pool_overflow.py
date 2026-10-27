"""E0318: an alloc that does not fit its pool, reported when labels resolve
in PARSE mode (the LSP and fluff allocate pools locally; a build reports
the same overflow at link as E0404)."""

from __future__ import annotations

import pytest

from a816.parse.nodes import NodeError
from a816.program import Program

_OVERFLOW_SRC = ".pool p { range 0x008000 0x008003 }\n.alloc foo in p {\n    .db 1, 2, 3, 4, 5, 6\n}\n"
_EXHAUSTED_SRC = (
    ".pool p { range 0x008000 0x008007 }\n"
    ".alloc a in p {\n    .db 1, 2, 3, 4, 5, 6\n}\n"
    ".alloc b in p {\n    .db 1, 2, 3, 4\n}\n"
)


def _overflow(src: str) -> NodeError:
    program = Program()
    error, nodes = program.parser.parse(src, "pool.s")
    assert error is None, error
    with pytest.raises(NodeError) as excinfo:
        program.resolve_labels(nodes)
    return excinfo.value


def test_overflow_code() -> None:
    assert _overflow(_OVERFLOW_SRC).code == "E0318"


def test_overflow_points_at_the_alloc_name() -> None:
    token = _overflow(_OVERFLOW_SRC).file_info
    assert token is not None
    assert token.value == "foo"


def test_overflow_names_the_largest_free_chunk() -> None:
    assert "largest free chunk is 2 bytes" in str(_overflow(_EXHAUSTED_SRC))


def test_an_alloc_larger_than_the_pool_says_so() -> None:
    assert "larger than the pool (4 bytes)" in str(_overflow(_OVERFLOW_SRC))


def test_an_alloc_larger_than_the_pool_hints_at_splitting() -> None:
    assert "split 'foo'" in (_overflow(_OVERFLOW_SRC).hint or "")
