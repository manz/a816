"""A `.for` variable is a known constant to `:=` inside the loop body.

The loop variable was bound only when the iteration's nodes were emitted,
but `:=` evaluates while expanding, so `_c := i << 8` was E0210 "`i` is
not defined yet" (cacheguard, a CRC16 table computed at assembly time).
"""

from __future__ import annotations

from pathlib import Path

from a816.module_builder import build_with_imports
from a816.program import Program
from tests import StubWriter


def _bytes(source: str) -> bytes:
    writer = StubWriter()
    Program().assemble_string_with_emitter(source, "for.s", writer)
    return b"".join(writer.data)


def test_a_loop_variable_feeds_an_assign() -> None:
    source = "*= 0x008000\n.for i := 0, 3 {\n    _c := i << 8\n    .dw _c\n}\n"

    assert _bytes(source) == bytes([0x00, 0x00, 0x00, 0x01, 0x00, 0x02])


def test_assigns_chain_inside_a_loop() -> None:
    """The CRC shape: each step reads the previous `:=`."""
    source = "*= 0x008000\n.for i := 1, 3 {\n    _a := i * 3\n    _b := _a + 1\n    .db _b\n}\n"

    assert _bytes(source) == bytes([4, 7])


def test_nested_loops_see_both_variables() -> None:
    source = "*= 0x008000\n.for i := 0, 2 {\n    .for j := 0, 2 {\n        _v := i * 2 + j\n        .db _v\n    }\n}\n"

    assert _bytes(source) == bytes([0, 1, 2, 3])


def test_a_loop_variable_feeds_an_assign_in_object_mode(tmp_path: Path) -> None:
    main = tmp_path / "main.s"
    main.write_text(
        ".alloc table at 0x008000 {\n    .for i := 0, 3 {\n        _c := i << 8\n        .dw _c\n    }\n}\n",
        encoding="utf-8",
    )
    result = build_with_imports(main, tmp_path / "out.sfc", output_dir=tmp_path / "obj", output_format="sfc")

    assert result.exit_code == 0
    assert (tmp_path / "out.sfc").read_bytes()[:6] == bytes([0x00, 0x00, 0x00, 0x01, 0x00, 0x02])


def test_an_if_on_a_loop_variable_sees_it() -> None:
    """`.if` evaluates while expanding too: it read `i` as undefined, so false, every pass."""
    source = (
        "*= 0x008000\n.for i := 0, 3 {\n    .if i == 1 {\n        .db 0xAA\n    } else {\n        .db 0x00\n    }\n}\n"
    )

    assert _bytes(source) == bytes([0x00, 0xAA, 0x00])
