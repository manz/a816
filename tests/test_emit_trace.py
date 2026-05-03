import re
import tempfile
from pathlib import Path

import pytest

from a816.linker import Linker
from a816.object_file import ObjectFile
from a816.program import Program


def _build_two_region_linked() -> tuple[Path, ObjectFile, str]:
    source = """*=0x008000
.db 0x11, 0x22
*=0x028000
.db 0x33
"""
    tmpdir = Path(tempfile.mkdtemp(prefix="emittrace_"))
    asm = tmpdir / "two.s"
    obj = tmpdir / "two.o"
    asm.write_text(source)
    assert Program().assemble_as_object(str(asm), obj) == 0
    linked = Linker([ObjectFile.from_file(str(obj))]).link()
    return tmpdir, linked, str(asm)


def test_emit_trace_off_no_log(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("A816_EMIT_TRACE", raising=False)
    tmpdir, linked, _ = _build_two_region_linked()
    ips_path = tmpdir / "out.ips"
    assert Program().link_as_patch(linked, ips_path) == 0
    assert not (tmpdir / "out.ips.emit.log").exists()


def test_emit_trace_on_writes_log(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A816_EMIT_TRACE", "1")
    tmpdir, linked, asm_path = _build_two_region_linked()
    ips_path = tmpdir / "out.ips"
    assert Program().link_as_patch(linked, ips_path) == 0
    log_path = tmpdir / "out.ips.emit.log"
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    pattern = re.compile(r"snes=\$[0-9A-F]{6}\s+phys=0x[0-9A-F]{6}\s+size=\d+\s+src=")
    for line in lines:
        assert pattern.match(line), line
    assert "snes=$008000" in lines[0]
    assert "phys=0x000000" in lines[0]
    assert "size=2" in lines[0]
    assert "snes=$028000" in lines[1]
    assert "phys=0x010000" in lines[1]
    assert "size=1" in lines[1]


def test_emit_trace_unknown_src_when_no_lines(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("A816_EMIT_TRACE", "1")
    from a816.object_file import Region

    region = Region(base_address=0x008000, code=b"\xea\xea")
    linked = ObjectFile([region], [], files=[])
    ips_path = tmp_path / "out.ips"
    assert Program().link_as_patch(linked, ips_path) == 0
    log_path = tmp_path / "out.ips.emit.log"
    assert log_path.exists()
    content = log_path.read_text(encoding="utf-8")
    assert "src=<unknown>" in content


def test_emit_trace_direct_assemble_as_patch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("A816_EMIT_TRACE", "1")
    source = """*=0x008000
.db 0x11, 0x22
*=0x028000
.db 0x33
"""
    asm = tmp_path / "two.s"
    asm.write_text(source)
    ips_path = tmp_path / "out.ips"
    assert Program().assemble_as_patch(str(asm), ips_path) == 0
    log_path = tmp_path / "out.ips.emit.log"
    assert log_path.exists()
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2
    assert any("phys=0x000000" in line and "size=2" in line for line in lines)
    assert any("phys=0x010000" in line and "size=1" in line for line in lines)


def test_emit_trace_sfc_parity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("A816_EMIT_TRACE", "1")
    tmpdir, linked, _ = _build_two_region_linked()
    sfc_path = tmpdir / "out.sfc"
    assert Program().link_as_sfc(linked, sfc_path) == 0
    log_path = tmpdir / "out.sfc.emit.log"
    assert log_path.exists()
    assert len(log_path.read_text(encoding="utf-8").splitlines()) == 2
