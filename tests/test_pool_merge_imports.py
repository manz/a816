"""Two imported modules may each contribute ranges to one pool, as the linker allows.

Under `a816 build` the importer used to reject the second declaration
("already declared with different shape") while linking the same two
objects directly unioned the ranges: one rule per path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.module_builder import ModuleBuilder

MAIN = '.import "a"\n.import "b"\n.alloc main_code at 0x008000 {\nmain:\n    jsl.l a_fn\n    jsl.l b_fn\n    rtl\n}\n'


def _module(name: str, pool_header: str, value: int) -> str:
    return f"{pool_header}\n.alloc {name}_code in slack {{\n{name}_fn:\n    lda.b #0x{value:02x}\n    rtl\n}}\n"


def _build(root: Path, a_pool: str, b_pool: str) -> bytes:
    (root / "a.s").write_text(_module("a", a_pool, 0x11), encoding="utf-8")
    (root / "b.s").write_text(_module("b", b_pool, 0x22), encoding="utf-8")
    main = root / "main.s"
    main.write_text(MAIN, encoding="utf-8")
    obj = ModuleBuilder(module_paths=[root], include_paths=[root], output_dir=root / "obj").build(main)
    return b"".join(section.code for section in obj.sections)


def test_complementary_ranges_merge(tmp_path: Path) -> None:
    code = _build(
        tmp_path,
        ".pool slack { range 0x018000 0x0180ff }",
        ".pool slack { range 0x028000 0x0280ff }",
    )

    assert b"\xa9\x11\x6b" in code
    assert b"\xa9\x22\x6b" in code


def test_an_identical_redeclaration_still_merges(tmp_path: Path) -> None:
    pool = ".pool slack { range 0x018000 0x0180ff }"

    code = _build(tmp_path, pool, pool)

    assert b"\xa9\x22\x6b" in code


def test_a_different_fill_is_still_rejected(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(RuntimeError):
        _build(
            tmp_path,
            ".pool slack { range 0x018000 0x0180ff  fill 0x00 }",
            ".pool slack { range 0x028000 0x0280ff  fill 0xff }",
        )

    assert "already declared with a different fill" in caplog.text


def test_overlapping_ranges_are_rejected(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(RuntimeError):
        _build(
            tmp_path,
            ".pool slack { range 0x018000 0x0180ff }",
            ".pool slack { range 0x018080 0x01817f }",
        )

    assert "overlaps existing" in caplog.text
