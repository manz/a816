"""A pool places its floating blocks around every pin inside its ranges, not only the pins it owns."""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.module_builder import BuildResult, build_with_imports
from a816.pool import Pool, PoolRange
from tests import BANK_40_MAP
from tests.test_reserve import _link, _symbols

_POOL = ".pool p {\n    range 0x408000 0x40800f\n}\n"
_FLOATING = ".alloc floating in p {\n    .db 9, 9\n}\n"


def _build(tmp_path: Path, files: dict[str, str]) -> BuildResult:
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    return build_with_imports(
        tmp_path / "main.s", tmp_path / "out.ips", module_paths=[tmp_path], output_dir=tmp_path / "obj"
    )


def _floating(tmp_path: Path, files: dict[str, str]) -> int | None:
    result = _build(tmp_path, files)
    assert result.exit_code == 0, result.diagnostics
    return result.symbol_map.get("floating")


@pytest.mark.parametrize(
    "pin",
    [
        ".alloc pinned at 0x408000 {\n    .db 1, 2, 3, 4\n}\n",
        ".alloc at 0x408000 {\n    .db 1, 2, 3, 4\n}\n",
        "*=0x408000\n    .db 1, 2, 3, 4\n",
    ],
    ids=["named pin", "anonymous pin", "star-equals"],
)
def test_a_floating_block_goes_around_a_pin_the_pool_does_not_own(tmp_path: Path, pin: str) -> None:
    assert _floating(tmp_path, {"main.s": BANK_40_MAP + _POOL + pin + _FLOATING}) == 0x408004


def test_a_pin_from_another_module_is_kept_clear(tmp_path: Path) -> None:
    files = {
        "main.s": BANK_40_MAP + '.import "pins"\n' + _POOL + _FLOATING,
        "pins.s": ".alloc pinned at 0x408000 {\n    .db 1, 2, 3, 4\n}\n",
    }
    assert _floating(tmp_path, files) == 0x408004


def test_a_pin_in_another_pool_is_kept_clear(tmp_path: Path) -> None:
    other = ".pool q {\n    range 0x408000 0x4080ff\n}\n.alloc pinned at 0x408000 in q {\n    .db 1, 2, 3, 4\n}\n"
    assert _floating(tmp_path, {"main.s": BANK_40_MAP + _POOL + other + _FLOATING}) == 0x408004


def test_a_pin_in_the_middle_splits_the_pool(tmp_path: Path) -> None:
    pin = ".alloc at 0x408002 {\n    .db 1, 2\n}\n"
    big = ".alloc floating in p {\n    .db 9, 9, 9\n}\n"
    assert _floating(tmp_path, {"main.s": BANK_40_MAP + _POOL + pin + big}) == 0x408004


def test_contexts_of_one_pool_still_share_memory(tmp_path: Path) -> None:
    src = (
        ".pool menu_ram { bss  range 0x7e9800 0x7e980f  contexts a, b }\n"
        ".reserve held 4 at 0x7e9800 in menu_ram.a\n"
        ".reserve shared 4 in menu_ram.b\n"
    )
    assert _symbols(_link(src, str(tmp_path)))["shared"] == 0x7E9800


def test_two_pins_on_the_same_bytes_still_fail(tmp_path: Path) -> None:
    pins = ".alloc at 0x408000 in p {\n    .db 1, 2, 3, 4\n}\n.alloc other at 0x408002 {\n    .db 9, 9\n}\n"
    result = _build(tmp_path, {"main.s": BANK_40_MAP + _POOL + pins})
    assert "E0408" in result.diagnostics[0]


def _pool() -> Pool:
    pool = Pool("p", [PoolRange(start=0x8000, end=0x800F)])
    pool.occupy(0x8000, 0x8003)
    return pool


def test_occupied_bytes_still_count_in_capacity() -> None:
    assert _pool().capacity == 16


def test_occupied_bytes_are_not_free() -> None:
    assert _pool().free == 12


def test_a_pool_cannot_be_occupied_after_allocation() -> None:
    pool = _pool()
    pool.allocate()
    with pytest.raises(Exception, match="already allocated; cannot occupy"):
        pool.occupy(0x8008, 0x8009)
