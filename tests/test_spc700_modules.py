"""SPC700 modules: objects carry their CPU, the linker writes their operands and refuses to mix CPUs."""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.build_cache import BuildSettings
from a816.cli import build_main
from a816.exceptions import ArchMismatchLinkError
from a816.linker import Linker
from a816.module_builder import build_with_imports
from a816.object_file import ObjectFile
from a816.program import Program

_ARAM = ".pool code {\n range 0x0400 0x0fff\n strategy order\n}\n"

_DSP = """.import "aram"
.alloc dsp in code {
dsp_write:
    mov 0xf2, a
    ret
status:
    .db 0
}
"""

_MAIN = """.import "aram"
.import "dsp"
.alloc main in code {
entry:
    call !dsp_write
    mov1 c, status.5
    mov a, !status
    bra dsp_write
    bne entry
}
"""


def _project(tmp_path: Path, main: str = _MAIN) -> Path:
    (tmp_path / "aram.s").write_text(_ARAM, encoding="utf-8")
    (tmp_path / "dsp.s").write_text(_DSP, encoding="utf-8")
    source = tmp_path / "main.s"
    source.write_text(main, encoding="utf-8")
    return source


def _build(tmp_path: Path, main: str = _MAIN) -> tuple[int, dict[str, int], bytes]:
    source = _project(tmp_path, main)
    output = tmp_path / "driver.bin"
    result = build_with_imports(
        source, output, "aram", output_dir=tmp_path / "obj", use_a816_toml=False, use_cache=False, arch="spc700"
    )
    return result.exit_code, result.symbol_map, output.read_bytes() if output.exists() else b""


def test_modules_link_into_one_aram_image(tmp_path: Path) -> None:
    exit_code, symbols, image = _build(tmp_path)
    assert exit_code == 0
    base = min(symbols["dsp_write"], symbols["entry"])
    entry = symbols["entry"] - base
    status = symbols["status"]
    branch_end = symbols["entry"] + 11
    expected = (
        bytes([0x3F]) + symbols["dsp_write"].to_bytes(2, "little")
        + bytes([0xAA]) + (status | 5 << 13).to_bytes(2, "little")
        + bytes([0xE5]) + status.to_bytes(2, "little")
        + bytes([0x2F, (symbols["dsp_write"] - branch_end) & 0xFF])
        + bytes([0xD0, (symbols["entry"] - (branch_end + 2)) & 0xFF])
    )  # fmt: skip
    assert image[entry : entry + len(expected)] == expected


def test_an_imported_label_without_bang_is_an_error(tmp_path: Path) -> None:
    source = _project(tmp_path, _MAIN.replace("mov a, !status", "mov a, status"))
    result = build_with_imports(
        source, tmp_path / "o.bin", "aram", output_dir=tmp_path / "obj", use_a816_toml=False, arch="spc700"
    )
    assert "status is only known at link" in "\n".join(result.diagnostics)


def test_the_object_carries_its_arch(tmp_path: Path) -> None:
    _build(tmp_path)
    assert ObjectFile.from_file(str(tmp_path / "obj" / "dsp.o")).arch == "spc700"


def test_objects_for_two_cpus_do_not_link() -> None:
    with pytest.raises(ArchMismatchLinkError, match="different CPUs"):
        Linker([ObjectFile(b"", [], arch="spc700"), ObjectFile(b"", [], arch="65c816")]).link()


@pytest.mark.parametrize(("arch", "output_format"), [("spc700", "ips"), ("65c816", "aram")])
def test_an_output_format_for_the_other_cpu_is_refused(tmp_path: Path, arch: str, output_format: str) -> None:
    with pytest.raises(ArchMismatchLinkError, match="does not fit"):
        Program().link_as(output_format, ObjectFile(b"", [], arch=arch), tmp_path / "out")


def test_the_arch_keys_the_cache() -> None:
    def settings(arch: str) -> BuildSettings:
        return BuildSettings({}, [], [], [], [], source_date_epoch=None, arch=arch)

    assert settings("spc700").digest() != settings("65c816").digest()


def test_a_65c816_build_keeps_its_cache_key() -> None:
    assert BuildSettings({}, [], [], [], [], source_date_epoch=None).digest() == (
        BuildSettings({}, [], [], [], [], source_date_epoch=None, arch="65c816").digest()
    )


def test_the_cli_links_an_aram_image_by_default(tmp_path: Path) -> None:
    source = tmp_path / "driver.s"
    source.write_text(".alloc main at 0x0400 {\n    mov a, #VALUE\n    ret\n}\n", encoding="utf-8")
    output = tmp_path / "driver.bin"
    argv = ["--arch", "spc700", str(source), "-o", str(output), "--obj-dir", str(tmp_path / "obj"), "-D", "VALUE=7"]
    assert build_main(argv) == 0
    assert output.read_bytes() == b"\xe8\x07\x6f"
