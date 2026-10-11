"""An `.alloc` body is its own scope for `_` names.

A `.assert` written inside the body sees the body's `_` labels, in an
object build (where they export under the `__sc<idx>__` mangle) and in a
direct one. Outside the body they stay private, and the failure says so.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.debug_info import read as read_debug_info
from a816.exceptions import LinkAssertError
from a816.linker import Linker
from a816.module_builder import build_with_imports
from a816.object_file import ObjectFile
from a816.program import Program
from tests import BANK_40_MAP

_POOL = ".pool g { range 0x500000 0x50ffff }\n"
_INSIDE = '.alloc tbl in g {\n    .dw 1, 2, 3\n_end:\n    .assert _end - tbl == 6, "inside"\n}\n'
_BEFORE = '.alloc tbl in g {\n    .assert _end - tbl == 6, "before"\n    .dw 1, 2, 3\n_end:\n}\n'
_OUTSIDE = '.alloc tbl in g {\n    .dw 1, 2, 3\n_end:\n}\n.assert _end - tbl == 6, "outside"\n'


def _link(tmp_path: Path, source: str) -> None:
    (tmp_path / "m.s").write_text(BANK_40_MAP + _POOL + source)
    assert Program().assemble_as_object(str(tmp_path / "m.s"), tmp_path / "m.o") == 0
    Linker([ObjectFile.from_file(str(tmp_path / "m.o"))]).link(base_address=0x8000)


def _direct(tmp_path: Path, source: str) -> int:
    (tmp_path / "m.s").write_text(BANK_40_MAP + _POOL + source)
    return Program().assemble(str(tmp_path / "m.s"), tmp_path / "m.sfc")


def test_an_assert_in_the_body_sees_its_private_label(tmp_path: Path) -> None:
    _link(tmp_path, _INSIDE)


def test_an_assert_sees_a_private_label_defined_after_it(tmp_path: Path) -> None:
    _link(tmp_path, _BEFORE)


def test_a_false_assert_in_the_body_still_fails(tmp_path: Path) -> None:
    false_assert = _INSIDE.replace("== 6", "== 7")

    with pytest.raises(LinkAssertError):
        _link(tmp_path, false_assert)


def test_two_allocs_assert_on_their_own_private_label(tmp_path: Path) -> None:
    second = _INSIDE.replace("tbl", "tbl2").replace(".dw 1, 2, 3", ".dw 1")
    _link(tmp_path, _INSIDE + second.replace("== 6", "== 2"))


def test_outside_the_body_the_label_stays_private(tmp_path: Path) -> None:
    with pytest.raises(LinkAssertError) as excinfo:
        _link(tmp_path, _OUTSIDE)

    assert "private to its `.alloc` body" in excinfo.value.format()


def test_a_direct_build_sees_the_private_label(tmp_path: Path) -> None:
    assert _direct(tmp_path, _INSIDE) == 0


def test_a_direct_build_keeps_it_private_outside(tmp_path: Path) -> None:
    assert _direct(tmp_path, _OUTSIDE) == 128


_LOOP = ".alloc {name} at {addr} {{\n_loop:\n    bra _loop\n}}\n"


def _adbg_locals(tmp_path: Path) -> dict[str, int]:
    (tmp_path / "battle.s").write_text(_LOOP.format(name="battle_hook", addr="0x009000"))
    (tmp_path / "menu.s").write_text(_LOOP.format(name="menu_hook", addr="0x00A000"))
    (tmp_path / "main.s").write_text('.import "battle"\n.import "menu"\n')
    result = build_with_imports(
        main_source=tmp_path / "main.s",
        output_file=tmp_path / "out.ips",
        output_format="ips",
        output_dir=tmp_path / "obj",
    )
    assert result.exit_code == 0, result.diagnostics
    assert result.debug_info_path is not None
    return {s.name: s.address for s in read_debug_info(result.debug_info_path).symbols if "_loop" in s.name}


def test_adbg_names_a_private_label_by_its_module(tmp_path: Path) -> None:
    assert _adbg_locals(tmp_path) == {"battle._loop": 0x009000, "menu._loop": 0x00A000}


def test_a_linked_object_falls_back_to_its_file_name(tmp_path: Path) -> None:
    (tmp_path / "battle.s").write_text(_LOOP.format(name="battle_hook", addr="0x009000"))
    assert Program().assemble_as_object(str(tmp_path / "battle.s"), tmp_path / "battle.o") == 0

    linked = Linker([ObjectFile.from_file(str(tmp_path / "battle.o"))]).link(base_address=0x8000)

    assert [name for name, *_ in linked.symbols if "_loop" in name] == ["battle._loop"]
