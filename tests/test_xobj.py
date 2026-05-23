import json
from pathlib import Path

import pytest

from a816.object_file import ObjectFile, RelocationType, Section, SymbolSection, SymbolType
from a816.xobj import main


def _make_fixture(path: Path) -> Path:
    section = Section.anonymous_pinned(
        base_address=0x008000,
        code=b"\xea\xea",
        relocations=[(0x00, "ext_sym", RelocationType.ABSOLUTE_16)],
        expression_relocations=[(0x01, "foo + 1", 2)],
        lines=[(0x00, 0, 12, 4, 0)],
    )
    obj = ObjectFile(
        [section],
        [("foo", 0x008000, SymbolType.GLOBAL, SymbolSection.CODE)],
        files=["fixture.s"],
    )
    obj.write(str(path))
    return path


def test_summary_default(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _make_fixture(tmp_path / "fix.o")
    rc = main([str(p), "--summary"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "sections: 1" in captured.out
    assert "symbols: 1" in captured.out
    assert "version: 8" in captured.out


def test_json_roundtrip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _make_fixture(tmp_path / "fix.o")
    rc = main([str(p), "--json"])
    captured = capsys.readouterr()
    assert rc == 0
    data = json.loads(captured.out)
    assert data["version"] == 8
    assert data["sections"][0]["base_address"] == 0x008000
    assert bytes.fromhex(data["sections"][0]["code"]) == b"\xea\xea"
    assert data["symbols"][0]["type"] == "GLOBAL"
    assert data["symbols"][0]["section"] == "CODE"
    assert data["sections"][0]["relocations"][0]["type"] == "ABSOLUTE_16"


def test_sections_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _make_fixture(tmp_path / "fix.o")
    rc = main([str(p), "--sections"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "$008000" in captured.out


def test_sections_with_bytes_dump(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _make_fixture(tmp_path / "fix.o")
    rc = main([str(p), "--sections", "--bytes", "2"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "ea ea" in captured.out


def test_symbols_sorted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _make_fixture(tmp_path / "fix.o")
    rc = main([str(p), "--symbols"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "foo" in captured.out
    assert "GLOBAL" in captured.out


def test_relocs_listed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _make_fixture(tmp_path / "fix.o")
    rc = main([str(p), "--relocs"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "ext_sym" in captured.out
    assert "foo + 1" in captured.out


def test_missing_file(capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["/nonexistent/path/that/does/not/exist.o"])
    captured = capsys.readouterr()
    assert rc == 2
    assert "/nonexistent/path/that/does/not/exist.o" in captured.err


def test_diff_two_files(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    a = _make_fixture(tmp_path / "a.o")
    section_b = Section.anonymous_pinned(base_address=0x018000, code=b"\xea\xea\xea")
    ObjectFile([section_b], []).write(str(tmp_path / "b.o"))
    rc = main([str(a), str(tmp_path / "b.o"), "--diff"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "sections:" in captured.out
    assert "$008000" in captured.out
    assert "$018000" in captured.out


def test_lines_table_listed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--lines` walks each section's debug-line table and prints the
    `(file:line:col)` per source-mapped byte. The fixture maps offset
    0 of the first section to fixture.s:12:4."""
    p = _make_fixture(tmp_path / "fix.o")
    rc = main([str(p), "--lines"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "fixture.s" in out
    assert "12" in out


def test_files_table_listed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--files` enumerates the debug-info file table; fixture.s is
    the only entry registered."""
    p = _make_fixture(tmp_path / "fix.o")
    rc = main([str(p), "--files"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "fixture.s" in out


def test_aliases_section_listed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Aliases (deferred constant bindings) round-trip through `--aliases`."""
    section = Section.anonymous_pinned(base_address=0x008000, code=b"\xea")
    obj = ObjectFile([section], [], aliases=[("font_ptr", "target + 0x40")])
    obj.write(str(tmp_path / "fix.o"))
    rc = main([str(tmp_path / "fix.o"), "--aliases"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "font_ptr" in out
    assert "target + 0x40" in out


def test_all_includes_every_section(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--all` is a convenience for sections + symbols + relocs + lines
    + files + aliases at once. Single fixture exercising the union."""
    p = _make_fixture(tmp_path / "fix.o")
    rc = main([str(p), "--all"])
    out = capsys.readouterr().out
    assert rc == 0
    # Each subsection's header (or representative content) shows up.
    assert "[0] base=" in out
    assert "foo" in out  # symbol
    assert "ext_sym" in out  # relocation
    assert "fixture.s" in out  # debug line / file


def test_bytes_dump_prefix(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`--bytes N` prepends a hex dump of the first N bytes per section."""
    section = Section.anonymous_pinned(base_address=0x008000, code=bytes(range(32)))
    ObjectFile([section], []).write(str(tmp_path / "fix.o"))
    rc = main([str(tmp_path / "fix.o"), "--sections", "--bytes", "8"])
    out = capsys.readouterr().out
    assert rc == 0
    # Eight bytes from `bytes(range(32))` — 00..07 — in the dump.
    assert "00 01 02 03 04 05 06 07" in out


def test_diff_marks_size_change(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Sections sharing a base but differing in size are flagged `!`
    by the diff renderer."""
    section_a = Section.anonymous_pinned(base_address=0x008000, code=b"\xea\xea")
    section_b = Section.anonymous_pinned(base_address=0x008000, code=b"\xea\xea\xea")
    ObjectFile([section_a], []).write(str(tmp_path / "a.o"))
    ObjectFile([section_b], []).write(str(tmp_path / "b.o"))
    rc = main([str(tmp_path / "a.o"), str(tmp_path / "b.o"), "--diff"])
    out = capsys.readouterr().out
    assert rc == 0
    # Output marks the differing section row, naming both byte counts.
    assert "! section" in out
    assert "size 2 -> 3" in out


def test_diff_handles_added_and_removed_sections(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A second section in B (none in A) shows up as `+`, and vice
    versa as `-`."""
    section_only_in_b = Section.anonymous_pinned(base_address=0x028000, code=b"\xea")
    ObjectFile([], []).write(str(tmp_path / "a.o"))
    ObjectFile([section_only_in_b], []).write(str(tmp_path / "b.o"))
    rc = main([str(tmp_path / "a.o"), str(tmp_path / "b.o"), "--diff"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "+ section[0] only in B" in out

    # Reverse direction.
    rc = main([str(tmp_path / "b.o"), str(tmp_path / "a.o"), "--diff"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "- section[0] only in A" in out
