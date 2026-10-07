"""A typed cast over a module's own pooled label relocates like the plain arithmetic it stands for."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from a816.module_builder import BuildResult, build_with_imports
from tests.test_reserve import _PREAMBLE

_STRUCT = ".struct T {\n    byte lo\n    byte bank\n}\n"
# `pin` takes the pool's first bytes, so `tbl` links somewhere other than
# where its own module provisionally placed it.
_PIN = _PREAMBLE + ".alloc pin at 0xc10000 in code {\n    .db 0, 0, 0, 0, 0, 0, 0, 0\n}\n"


def _build(root: Path, use: str) -> BuildResult:
    (root / "pin.s").write_text(_PIN, encoding="utf-8")
    user = '.import "pin"\n' + _STRUCT + ".alloc tbl in code {\n    .db 1, 2\n}\n.alloc user in code {\n" + use + "}\n"
    (root / "main.s").write_text(user, encoding="utf-8")
    return build_with_imports(root / "main.s", root / "out.ips", module_paths=[root], output_dir=root / "obj")


def _operand(root: Path, result: BuildResult) -> int:
    data = (root / "out.ips").read_bytes()
    address = result.symbol_map["user"]
    user = ((address >> 16) - 0xC0) * 0x10000 + (address & 0xFFFF)  # HiROM file offset
    i = 5
    while data[i : i + 3] != b"EOF":
        offset = int.from_bytes(data[i : i + 3], "big")
        size = int.from_bytes(data[i + 3 : i + 5], "big")
        body = data[i + 5 : i + 5 + size]
        if offset <= user < offset + size:
            at = user - offset
            return int.from_bytes(body[at + 1 : at + 4], "little")
        i += 5 + size
    raise AssertionError("user not found in the patch")


@pytest.mark.parametrize(
    "use",
    ["    lda.l (tbl as T).bank, x\n", "    lda.l (tbl + 0 as T).bank, x\n"],
    ids=["cast access", "cast over an expression"],
)
def test_a_cast_over_a_pooled_label_uses_its_linked_address(tmp_path: Path, use: str) -> None:
    result = _build(tmp_path, use)
    assert result.exit_code == 0, result.diagnostics
    assert _operand(tmp_path, result) == result.symbol_map["tbl"] + 1


def _build_view(root: Path, bind: str) -> BuildResult:
    (root / "pin.s").write_text(_PIN, encoding="utf-8")
    main = (
        '.import "pin"\n'
        + _STRUCT
        + f".alloc tbl in code {{\n    .db 1, 2\n}}\n{bind}\n"
        + ".alloc user in code {\n    lda.l view.bank, x\n}\n"
    )
    (root / "main.s").write_text(main, encoding="utf-8")
    return build_with_imports(root / "main.s", root / "out.ips", module_paths=[root], output_dir=root / "obj")


def test_a_lazy_typed_view_over_a_pooled_label_uses_its_linked_address(tmp_path: Path) -> None:
    result = _build_view(tmp_path, "view = (tbl as T)")
    assert result.exit_code == 0, result.diagnostics
    assert _operand(tmp_path, result) == result.symbol_map["tbl"] + 1


def test_an_eager_typed_bind_over_a_pooled_label_points_at_the_lazy_form(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR):
        result = _build_view(tmp_path, "view := (tbl as T)")
    assert (result.exit_code, "use `=` for a forward reference" in caplog.text) == (1, True)


def test_a_lazy_typed_view_of_an_unknown_struct_says_so() -> None:
    from a816.parse.nodes import NodeError
    from a816.program import Program
    from tests import StubWriter

    program, writer = Program(), StubWriter()
    with pytest.raises(NodeError, match="unknown struct type 'Nope'"):
        program.assemble_string_with_emitter("view = (0x7e1000 as Nope)\n", "m.s", writer)


def test_a_lazy_typed_view_over_a_constant() -> None:
    from a816.program import Program
    from tests import StubWriter

    writer = StubWriter()
    src = _STRUCT + "view = (0x7e1000 as T)\n*=0x8000\n    .dl view.bank\n"
    Program().assemble_string_with_emitter(src, "m.s", writer)
    assert b"".join(writer.data) == b"\x01\x10\x7e"
