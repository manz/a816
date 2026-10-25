"""`@std/snes/spc` (the S-CPU's IPL upload) and `@std/spc700/dsp` (SPC700 I/O and S-DSP)."""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.cli import build_main
from a816.cpu.arch import using_arch
from a816.lsp.workspace import WorkspaceIndex
from a816.module_loader import resolve_module

_MAIN = """.import "@std/snes/spc"
.alloc sound_image at 0x018000 {
driver_image:
    .embed "driver"
}
.alloc boot at 0x008000 {
    spc_ipl_upload(driver_image, driver.__size, driver.__base, driver.start)
    rts
}
"""

_DRIVER = """.import "@std/spc700/dsp"
.alloc driver at 0x0400 {
start:
    dsp_write(DSP_FLG, 0x20)
    dsp_write(DSP_VOICE.SRCN + 2 * DSP_VOICE_STRIDE, 0)
    dsp_write_a(DSP_KON)
    dsp_read(DSP_ENDX)
    mov CPUIO0, a
    bra start
}
"""

_DRIVER_IMAGE = bytes.fromhex("8f6cf2 8f20f3 8f24f2 8f00f3 8f4cf2 c4f3 8f7cf2 e4f3 c4f4 2fe6")

_PORT = bytes.fromhex("2100")


def _upload(size: int) -> bytes:
    """The IPL upload of `size` bytes from $01:8000 to ARAM $0400, entered at $0400."""
    return b"".join(
        [
            bytes.fromhex("af40") + _PORT + bytes.fromhex("c9aa d0f8"),
            bytes.fromhex("af41") + _PORT + bytes.fromhex("c9bb d0f0"),
            bytes.fromhex("a900 8f42") + _PORT + bytes.fromhex("a904 8f43") + _PORT,
            bytes.fromhex("a901 8f41") + _PORT + bytes.fromhex("a9cc 8f40") + _PORT,
            bytes.fromhex("cf40") + _PORT + bytes.fromhex("d0fa a20000"),
            bytes.fromhex("bf008001 8f41") + _PORT + bytes.fromhex("8a 8f40") + _PORT,
            bytes.fromhex("cf40") + _PORT + bytes.fromhex("d0fa e8 e0") + size.to_bytes(2, "little"),
            bytes.fromhex("d0e7 a900 8f42") + _PORT + bytes.fromhex("a904 8f43") + _PORT,
            bytes.fromhex("a900 8f41") + _PORT + bytes.fromhex("8a 1a d001 1a 8f40") + _PORT,
            bytes.fromhex("cf40") + _PORT + bytes.fromhex("d0fa"),
        ]
    )


@pytest.fixture
def rom(tmp_path: Path) -> bytes:
    (tmp_path / "main.s").write_text(_MAIN, encoding="utf-8")
    (tmp_path / "driver.spc.s").write_text(_DRIVER, encoding="utf-8")
    output = tmp_path / "game.sfc"
    argv = [str(tmp_path / "main.s"), "-f", "sfc", "-o", str(output), "--obj-dir", str(tmp_path / "obj")]
    assert build_main(argv) == 0
    return output.read_bytes()


def test_the_dsp_macros_write_through_the_address_port(rom: bytes) -> None:
    assert rom[0x8000 : 0x8000 + len(_DRIVER_IMAGE)] == _DRIVER_IMAGE


def test_the_ipl_upload_streams_the_embedded_driver(rom: bytes) -> None:
    upload = _upload(len(_DRIVER_IMAGE))
    assert rom[: len(upload) + 1] == upload + b"\x60"


def test_an_spc700_import_finds_the_spc700_stdlib_module() -> None:
    with using_arch("spc700"):
        found = resolve_module("@std/spc700/dsp", ".s", [])
    assert found is not None
    assert found.name == "dsp.spc.s"


def test_65816_code_does_not_see_spc700_modules() -> None:
    assert resolve_module("@std/spc700/dsp", ".s", []) is None


def test_the_workspace_indexes_the_spc700_stdlib_module(tmp_path: Path) -> None:
    source = tmp_path / "driver.spc.s"
    source.write_text(_DRIVER, encoding="utf-8")
    (found,) = WorkspaceIndex(tmp_path)._extract_includes(source, _DRIVER)
    assert found.name == "dsp.spc.s"
