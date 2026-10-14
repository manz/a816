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
from a816.object_file import ObjectFile

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


def test_an_inlined_alias_is_not_exported_again_by_each_importer(tmp_path: Path) -> None:
    """cacheguard on the first #286: `EV_FLAGS = flags` in a definitions
    module showed up once per importing module in the .adbg (+507 symbols)."""
    (tmp_path / "state.s").write_text(".alloc state at 0x018000 {\nflags:\n    .db 0\n}\n", encoding="utf-8")
    (tmp_path / "defs.s").write_text('.import "state"\nEV_FLAGS = flags\n', encoding="utf-8")
    for name, address in (("a", "0x008000"), ("b", "0x009000")):
        (tmp_path / f"{name}.s").write_text(
            f'.import "defs"\n.alloc {name}_code at {address} {{\n    lda.l EV_FLAGS\n}}\n', encoding="utf-8"
        )
    (tmp_path / "main.s").write_text('.import "a"\n.import "b"\n', encoding="utf-8")
    result = build_with_imports(
        main_source=tmp_path / "main.s",
        output_file=tmp_path / "out.ips",
        output_format="ips",
        output_dir=tmp_path / "obj",
    )
    assert result.exit_code == 0, result.diagnostics

    owner_aliases = ObjectFile.from_file(str(tmp_path / "obj" / "defs.o")).aliases
    importer_aliases = ObjectFile.from_file(str(tmp_path / "obj" / "a.o")).aliases

    assert (owner_aliases.count(("EV_FLAGS", "flags")), importer_aliases.count(("EV_FLAGS", "flags"))) == (1, 1)


def test_an_alias_over_a_label_of_this_module_placed_later(tmp_path: Path) -> None:
    """cacheguard's real case: `spc_default_song` is a floating alloc of the
    same module, so its bank is known only after the block reading it."""
    code, diagnostics, data = _ips(
        tmp_path,
        ".pool sound { range 0x018000 0x01ffff }\n"
        "SONG_BANK = (song_data >> 16) & 0xFF\n"
        ".alloc code at 0x008000 {\n    .a8\n    lda #SONG_BANK\n}\n"
        ".alloc song_data in sound {\n    .db 1\n}\n",
    )

    assert code == 0, diagnostics
    assert b"\xa9\x01" in data


@pytest.fixture
def late_address(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> str:
    """An address operand naming a later block's label is still sized by a
    guess: absolute from the PC, direct page once placed."""
    code, _, _ = _ips(
        tmp_path, ".alloc code at 0x008000 {\n    lda later\n}\n.alloc dp_block at 0x000010 {\nlater:\n    .db 0\n}\n"
    )
    assert code != 0
    return caplog.text


def test_a_value_known_late_names_operand_widths(late_address: str) -> None:
    assert "given a 16-bit operand before its value was known, and the value needs 8-bit" in late_address


def test_its_hint_says_why_the_size_was_guessed(late_address: str) -> None:
    assert "comes from outside this block" in late_address
