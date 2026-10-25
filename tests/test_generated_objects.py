"""Objects a Python generator builds in memory link with the compiled modules.

The generator cannot see final addresses: it hands its object to
`build_with_imports(objects=...)` and the linker places it; source reads
its labels through `.extern`.
"""

from __future__ import annotations

from pathlib import Path

from a816.debug_info import read as read_debug
from a816.module_builder import BuildResult, build_with_imports
from a816.object_file import ObjectFile
from a816.program import Program

_MAIN = """.pool rom {
    range 0x018000 0x01ffff
}
.extern message
.alloc boot at 0x008000 {
    lda.l message
}
"""

_PINNED = """.alloc text at 0x018000 {
message:
    .db 0x11, 0x22
}
"""

_POOLED = """.pool rom {
    range 0x018000 0x01ffff
}
.alloc text in rom {
message:
    .db 0x11, 0x22
}
"""


def _generated(root: Path, source: str) -> ObjectFile:
    """What a generator hands over; built here from source, in memory for the build."""
    path = root / "gen" / "text.s"
    path.parent.mkdir(exist_ok=True)
    path.write_text(source, encoding="utf-8")
    Program().assemble_as_object(str(path), path.with_suffix(".o"))
    return ObjectFile.from_file(str(path.with_suffix(".o")))


def _build(root: Path, objects: dict[str, ObjectFile]) -> tuple[BuildResult, bytes]:
    main = root / "main.s"
    main.write_text(_MAIN, encoding="utf-8")
    output = root / "game.sfc"
    result = build_with_imports(main, output, "sfc", output_dir=root / "obj", use_a816_toml=False, objects=objects)
    return result, output.read_bytes() if result.exit_code == 0 else b""


def test_source_reads_a_generated_label(tmp_path: Path) -> None:
    _, rom = _build(tmp_path, {"text": _generated(tmp_path, _PINNED)})
    assert rom[:4] == bytes([0xAF, 0x00, 0x80, 0x01])


def test_a_generated_object_lands_at_its_placement(tmp_path: Path) -> None:
    _, rom = _build(tmp_path, {"text": _generated(tmp_path, _PINNED)})
    assert rom[0x8000:0x8002] == bytes([0x11, 0x22])


def test_a_generated_alloc_takes_a_pool_the_source_declares(tmp_path: Path) -> None:
    _, rom = _build(tmp_path, {"text": _generated(tmp_path, _POOLED)})
    assert rom[0x8000:0x8002] == bytes([0x11, 0x22])


def test_the_debug_info_names_the_generated_label(tmp_path: Path) -> None:
    result, _ = _build(tmp_path, {"text": _generated(tmp_path, _POOLED)})
    assert result.debug_info_path is not None
    assert "message" in {symbol.name for symbol in read_debug(result.debug_info_path).symbols}


def test_a_generated_object_links_again_unchanged(tmp_path: Path) -> None:
    objects = {"text": _generated(tmp_path, _POOLED)}
    _, first = _build(tmp_path, objects)
    _, second = _build(tmp_path, objects)
    assert second == first


def test_a_generated_object_cannot_take_a_module_name(tmp_path: Path) -> None:
    result, _ = _build(tmp_path, {"__main__": _generated(tmp_path, _PINNED)})
    assert "generated object '__main__' has the name of a source module" in result.diagnostics


def test_without_the_object_the_extern_is_unresolved(tmp_path: Path) -> None:
    result, _ = _build(tmp_path, {})
    assert result.exit_code == 1


def test_an_overlap_names_a_generated_object_without_lines(tmp_path: Path) -> None:
    obj = _generated(tmp_path, _PINNED.replace("0x018000", "0x008000"))
    obj.files = []
    for section in obj.sections:
        section.lines = []
    for request in obj.pool_allocs:
        request.source = ""
    result, _ = _build(tmp_path, {"assets/text": obj})
    assert "`text` (2 bytes from $00:8000) at assets/text" in "\n".join(result.diagnostics)
