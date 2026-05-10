import json
from pathlib import Path

import pytest

from a816.object_file import ObjectFile, Region, RelocationType, SymbolSection, SymbolType
from a816.xobj import main


def _make_fixture(path: Path) -> Path:
    region = Region(
        base_address=0x008000,
        code=b"\xea\xea",
        relocations=[(0x00, "ext_sym", RelocationType.ABSOLUTE_16)],
        expression_relocations=[(0x01, "foo + 1", 2)],
        lines=[(0x00, 0, 12, 4, 0)],
    )
    obj = ObjectFile(
        [region],
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
    assert "regions: 1" in captured.out
    assert "symbols: 1" in captured.out
    assert "version: 7" in captured.out


def test_json_roundtrip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _make_fixture(tmp_path / "fix.o")
    rc = main([str(p), "--json"])
    captured = capsys.readouterr()
    assert rc == 0
    data = json.loads(captured.out)
    assert data["version"] == 7
    assert data["regions"][0]["base_address"] == 0x008000
    assert bytes.fromhex(data["regions"][0]["code"]) == b"\xea\xea"
    assert data["symbols"][0]["type"] == "GLOBAL"
    assert data["symbols"][0]["section"] == "CODE"
    assert data["regions"][0]["relocations"][0]["type"] == "ABSOLUTE_16"


def test_regions_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _make_fixture(tmp_path / "fix.o")
    rc = main([str(p), "--regions"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "$008000" in captured.out


def test_regions_with_bytes_dump(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _make_fixture(tmp_path / "fix.o")
    rc = main([str(p), "--regions", "--bytes", "2"])
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
    region_b = Region(base_address=0x018000, code=b"\xea\xea\xea")
    ObjectFile([region_b], []).write(str(tmp_path / "b.o"))
    rc = main([str(a), str(tmp_path / "b.o"), "--diff"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "regions:" in captured.out
    assert "$008000" in captured.out
    assert "$018000" in captured.out
