"""E0408: two placed blocks writing the same ROM bytes are named as the source names them."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from a816.exceptions import EmittedBlock
from a816.module_builder import BuildResult, build_with_imports
from a816.program.block_overlaps import overlapping_blocks
from tests import BANK_40_MAP

_TWO_POOLS = (
    ".pool p {\n    range 0x408000 0x40800f\n}\n"
    ".pool q {\n    range 0x408000 0x40800f\n}\n"
    ".alloc first in p {\n    .db 1, 2, 3, 4\n}\n"
    ".alloc second in q {\n    .db 9, 9\n}\n"
)


def _build(tmp_path: Path, src: str, overlap_mode: str | None = None) -> BuildResult:
    (tmp_path / "main.s").write_text(BANK_40_MAP + src, encoding="utf-8")
    return build_with_imports(
        tmp_path / "main.s", tmp_path / "out.ips", output_dir=tmp_path / "obj", overlap_mode=overlap_mode
    )


def _error(tmp_path: Path, src: str) -> str:
    result = _build(tmp_path, src)
    assert result.exit_code != 0
    return result.diagnostics[0]


def test_two_pools_handing_out_the_same_bytes_name_both_allocs(tmp_path: Path) -> None:
    error = _error(tmp_path, _TWO_POOLS)
    assert error.splitlines()[0] == "linker error[E0408]: `second` in pool `q` overlaps `first` in pool `p`"


@pytest.mark.parametrize(
    "expected",
    [
        "`second` in pool `q` (2 bytes from $40:8000) at ",
        "`first` in pool `p` (4 bytes from $40:8000) at ",
        "shared: 2 bytes from $40:8000",
        "hint: two pools hand out the same bytes; keep their ranges apart",
    ],
)
def test_two_pools_handing_out_the_same_bytes_say_where_and_how_to_fix(tmp_path: Path, expected: str) -> None:
    assert expected in _error(tmp_path, _TWO_POOLS)


@pytest.mark.parametrize(("block", "line"), [("(2 bytes from $40:8000)", 11), ("(4 bytes from $40:8000)", 8)])
def test_each_block_points_at_its_source_line(tmp_path: Path, block: str, line: int) -> None:
    assert f"{block} at {tmp_path / 'main.s'}:{line}" in _error(tmp_path, _TWO_POOLS)


def test_anonymous_blocks_are_named_by_address(tmp_path: Path) -> None:
    src = ".alloc at 0x408000 {\n    .db 1, 2, 3, 4\n}\n.alloc at 0x408002 {\n    .db 9, 9\n}\n"
    assert "E0408]: block at $40:8000 overlaps block at $40:8002" in _error(tmp_path, src)


def test_two_pins_get_the_generic_hint(tmp_path: Path) -> None:
    src = ".alloc at 0x408000 {\n    .db 1, 2, 3, 4\n}\n.alloc at 0x408002 {\n    .db 9, 9\n}\n"
    assert "hint: move or shrink one of the blocks so their bytes stay apart" in _error(tmp_path, src)


def test_warn_mode_logs_the_overlap_and_builds(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    src = ".alloc at 0x408000 {\n    .db 1, 2, 3, 4\n}\n.alloc at 0x408002 {\n    .db 9, 9\n}\n"
    with caplog.at_level(logging.WARNING):
        result = _build(tmp_path, src, overlap_mode="warn")
    assert (result.exit_code, "E0408" in caplog.text) == (0, True)


def _block(name: str, start: int, end: int) -> EmittedBlock:
    return EmittedBlock(name=name, logical=start, start=start, end=end)


def test_every_overlapping_pair_is_reported() -> None:
    blocks = [_block("c", 4, 8), _block("a", 0, 6), _block("b", 2, 3), _block("d", 8, 9)]
    pairs = [(first.name, second.name) for first, second in overlapping_blocks(blocks)]
    assert pairs == [("a", "b"), ("a", "c")]
