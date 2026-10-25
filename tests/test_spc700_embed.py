"""`.embed`: an SPC700 program links inside the 65816 build that places its image.

The program is `<name>.spc.s` and its imports; the 65816 side reads its
labels as `<name>.<label>` plus `<name>.__base` / `<name>.__size`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.cli import build_main
from a816.module_builder import BuildResult, build_with_imports
from a816.object_file import ObjectFile
from a816.parse.nodes import NodeError
from a816.program import Program
from a816.writers import IPSWriter

_DRIVER = """.import "dsp"
.alloc driver at 0x0400 {
start:
    call !dsp_write
    bra start
}
"""

_DSP = """.alloc dsp at 0x0500 {
dsp_write:
    mov 0xf2, a
    ret
}
"""

_MAIN = """.alloc boot at 0x008000 {
    lda.w #driver.__base
    ldx.w #driver.__size
    jmp.w driver.start
}
.alloc image at 0x018000 {
    .embed "driver"
}
"""

_DRIVER_IMAGE = bytes([0x3F, 0x00, 0x05, 0x2F, 0xFB]) + bytes(0x100 - 5) + bytes([0xC4, 0xF2, 0x6F])


def _project(root: Path) -> Path:
    (root / "driver.spc.s").write_text(_DRIVER, encoding="utf-8")
    (root / "dsp.spc.s").write_text(_DSP, encoding="utf-8")
    main = root / "main.s"
    main.write_text(_MAIN, encoding="utf-8")
    return main


def _build(root: Path, main: Path, *, use_cache: bool = True) -> tuple[BuildResult, bytes]:
    output = root / "game.sfc"
    result = build_with_imports(main, output, "sfc", output_dir=root / "obj", use_a816_toml=False, use_cache=use_cache)
    return result, output.read_bytes() if output.exists() else b""


def test_the_image_lands_where_the_embed_is_placed(tmp_path: Path) -> None:
    _, rom = _build(tmp_path, _project(tmp_path), use_cache=False)
    assert rom[0x8000 : 0x8000 + len(_DRIVER_IMAGE)] == _DRIVER_IMAGE


def test_the_boot_code_reads_the_program_base_size_and_labels(tmp_path: Path) -> None:
    _, rom = _build(tmp_path, _project(tmp_path), use_cache=False)
    assert rom[:9] == bytes([0xA9, 0x00, 0x04, 0xA2, 0x03, 0x01, 0x4C, 0x00, 0x04])


def test_an_edited_program_rebuilds_its_embedder(tmp_path: Path) -> None:
    main = _project(tmp_path)
    _build(tmp_path, main)
    (tmp_path / "dsp.spc.s").write_text(_DSP.replace("0xf2", "0xf3"), encoding="utf-8")
    _, rom = _build(tmp_path, main)
    assert rom[0x8100:0x8102] == bytes([0xC4, 0xF3])


def test_a_warm_build_leaves_the_image_alone(tmp_path: Path) -> None:
    main = _project(tmp_path)
    _build(tmp_path, main)
    image = tmp_path / "obj" / "spc700" / "driver" / "driver.aram"
    written = image.stat().st_mtime_ns
    _build(tmp_path, main)
    assert image.stat().st_mtime_ns == written


def test_a_missing_program_fails_the_build(tmp_path: Path) -> None:
    main = _project(tmp_path)
    (tmp_path / "driver.spc.s").unlink()
    result, _ = _build(tmp_path, main, use_cache=False)
    assert "no SPC700 program driver.spc.s" in "\n".join(result.diagnostics)


def test_an_embed_outside_a_module_build_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(NodeError, match="driver"):
        Program().assemble_string_with_emitter(_MAIN, str(tmp_path / "main.s"), IPSWriter(None))  # type: ignore[arg-type]


def test_an_spc_source_builds_for_the_spc700_by_its_name(tmp_path: Path) -> None:
    _project(tmp_path)
    output = tmp_path / "driver.bin"
    result = build_with_imports(
        tmp_path / "driver.spc.s", output, "aram", output_dir=tmp_path / "obj", use_a816_toml=False
    )
    assert (result.exit_code, ObjectFile.from_file(str(tmp_path / "obj" / "dsp.o")).arch) == (0, "spc700")


def test_the_cli_links_an_spc_source_as_an_aram_image(tmp_path: Path) -> None:
    _project(tmp_path)
    output = tmp_path / "driver.bin"
    argv = [str(tmp_path / "driver.spc.s"), "-o", str(output), "--obj-dir", str(tmp_path / "obj")]
    assert build_main(argv) == 0
    assert output.read_bytes() == _DRIVER_IMAGE


def test_an_spc700_module_cannot_be_imported_into_65816_code(tmp_path: Path) -> None:
    main = _project(tmp_path)
    main.write_text('.import "dsp.spc"\n' + _MAIN, encoding="utf-8")
    result, _ = _build(tmp_path, main, use_cache=False)
    assert "with `.embed" in "\n".join(result.diagnostics)


def test_the_cartridge_board_does_not_map_an_spc700_build(tmp_path: Path) -> None:
    _project(tmp_path)
    (tmp_path / "a816.toml").write_text('board = "SHVC-1J3M-20"\nrom_size = 0x100000\n', encoding="utf-8")
    output = tmp_path / "driver.bin"
    build_with_imports(tmp_path / "driver.spc.s", output, "aram", output_dir=tmp_path / "obj")
    assert output.read_bytes() == _DRIVER_IMAGE


def _aram_build(root: Path, source: str) -> BuildResult:
    (root / "over.spc.s").write_text(source, encoding="utf-8")
    return build_with_imports(root / "over.spc.s", root / "over.bin", "aram", output_dir=root / "obj", use_a816_toml=False)


def test_a_placement_past_ffff_names_the_aram_limit(tmp_path: Path) -> None:
    result = _aram_build(tmp_path, ".alloc x at 0xFFFF {\n    .db 1, 2, 3\n}\n")
    assert "past the end of ARAM" in "\n".join(result.diagnostics)


def test_an_unbounded_spc700_alloc_ends_at_ffff(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("INFO"):
        _aram_build(tmp_path, ".alloc x at 0x0400 {\n    .db 1\n}\n")
    assert "leftover 0x000401..0x00ffff" in caplog.text
