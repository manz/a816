"""`.reclaim` gives its range to the pool in a build, not only at compile.

The object recorded a pool's ranges when `.pool` was declared; a later
`.reclaim` changed the compile-time pool only, and the linker, which
rebuilds pools from the objects, never saw the range (ff4, rc1: E0404
"larger than the pool" for an alloc the reclaimed space fits).
"""

from __future__ import annotations

from pathlib import Path

from a816.module_builder import build_with_imports

POOL = ".pool slack {\n    range 0x018000 0x018003\n    strategy order\n}\n"
RECLAIM = ".reclaim slack 0x018100 0x01811F\n"
ALLOC = ".alloc big in slack {\n    .db 1, 2, 3, 4, 5, 6, 7, 8, 9, 10\n}\n"


def _build(tmp_path: Path, files: dict[str, str]) -> tuple[int, dict[str, int]]:
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    result = build_with_imports(
        tmp_path / "main.s", tmp_path / "out.ips", module_paths=[tmp_path], output_dir=tmp_path / "obj"
    )
    return result.exit_code, result.symbol_map


def test_an_alloc_fits_the_reclaimed_range(tmp_path: Path) -> None:
    code, symbols = _build(tmp_path, {"main.s": POOL + RECLAIM + ALLOC})

    assert (code, symbols.get("big")) == (0, 0x018100)


def test_a_relocate_gives_its_old_range_to_the_pool(tmp_path: Path) -> None:
    """`.relocate` reclaims the routine's old bytes too: `moved` (1 byte) takes
    the pool's first range, and `big` only fits in the reclaimed one."""
    relocate = ".relocate moved 0x018100 0x01811F into slack {\n    rts\n}\n"
    code, symbols = _build(tmp_path, {"main.s": POOL + relocate + ALLOC})

    assert (code, symbols.get("big")) == (0, 0x018100)


def test_a_reclaim_in_another_module_reaches_the_pool(tmp_path: Path) -> None:
    files = {"main.s": '.import "slack"\n' + ALLOC, "slack.s": POOL + RECLAIM}
    code, symbols = _build(tmp_path, files)

    assert (code, symbols.get("big")) == (0, 0x018100)


def test_the_same_reclaim_reached_twice_is_harmless(tmp_path: Path) -> None:
    """ff4: a `.reclaim` in a header that a module and its import both include
    reclaimed the range twice in one compile, and the second was E0342."""
    (tmp_path / "slack.i").write_text(RECLAIM, encoding="utf-8")
    files = {"slack.s": POOL + '.include "slack.i"\n', "main.s": '.import "slack"\n.include "slack.i"\n' + ALLOC}
    code, symbols = _build(tmp_path, files)

    assert (code, symbols.get("big")) == (0, 0x018100)


def test_an_overlapping_reclaim_is_still_an_error(tmp_path: Path) -> None:
    code, _ = _build(tmp_path, {"main.s": POOL + RECLAIM + ".reclaim slack 0x018110 0x01812F\n" + ALLOC})

    assert code != 0
