"""`lda #NAME`, NAME an alias over an imported symbol, sizes like the
expression written inline.

cacheguard (1.1.1): `SONG_BANK = (spc_default_song >> 16) & 0xFF` then
`lda #SONG_BANK` in an `.a8` alloc failed with E0313 "`lda` was sized as 4
bytes before its target was placed", while `lda #(song >> 16) & 0xFF`
built. An import's names only became externs during resolution, after the
alloc body had been measured, so the alias did not exist yet and the PC
stood in for its value.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.module_builder import build_with_imports

_DATA = ".alloc song at 0x018000 {\n    .db 1\n}\n"


def _ips(tmp_path: Path, main: str) -> tuple[int, list[str], bytes]:
    (tmp_path / "data.s").write_text(_DATA, encoding="utf-8")
    (tmp_path / "main.s").write_text('.import "data"\n' + main, encoding="utf-8")
    result = build_with_imports(
        main_source=tmp_path / "main.s",
        output_file=tmp_path / "out.ips",
        output_format="ips",
        output_dir=tmp_path / "obj",
    )
    data = (tmp_path / "out.ips").read_bytes() if result.exit_code == 0 else b""
    return result.exit_code, [str(diagnostic) for diagnostic in result.diagnostics], data


def test_an_alias_over_an_import_sizes_like_its_expression(tmp_path: Path) -> None:
    code, diagnostics, data = _ips(
        tmp_path, "SONG_BANK = (song >> 16) & 0xFF\n.alloc code at 0x008000 {\n    .a8\n    lda #SONG_BANK\n}\n"
    )

    assert code == 0, diagnostics
    assert b"\xa9\x01" in data


def test_the_inline_expression_still_builds_the_same(tmp_path: Path) -> None:
    code, diagnostics, data = _ips(tmp_path, ".alloc code at 0x008000 {\n    .a8\n    lda #(song >> 16) & 0xFF\n}\n")

    assert code == 0, diagnostics
    assert b"\xa9\x01" in data


@pytest.fixture
def late_alias(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> str:
    """An alias over a label of a later block: still sized by a guess."""
    code, _, _ = _ips(
        tmp_path,
        ".alloc code at 0x008000 {\n    lda #LATER\n}\n"
        ".alloc tail_block at 0x018000 {\ntail:\n    .db 0\n}\n"
        "LATER = (tail >> 16) & 0xFF\n",
    )
    assert code != 0
    return caplog.text


def test_a_value_known_late_names_operand_widths(late_alias: str) -> None:
    assert "given a 16-bit operand before its value was known, and the value needs 8-bit" in late_alias


def test_its_hint_says_why_the_size_was_guessed(late_alias: str) -> None:
    assert "comes from outside this block" in late_alias
