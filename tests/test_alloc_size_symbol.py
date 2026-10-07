"""`.alloc NAME` publishes `NAME.__size`, its body's byte count, like `Type.__size` for a struct."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from a816.module_builder import BuildResult, build_with_imports
from tests.test_reserve import _PREAMBLE, _link, _symbols


def _sizes(src: str) -> dict[str, int]:
    with tempfile.TemporaryDirectory() as tmp:
        return _symbols(_link(src, tmp))


@pytest.mark.parametrize(
    ("src", "name", "size"),
    [
        (".alloc blob in code {\n    .db 1, 2, 3, 4, 5\n}\n", "blob.__size", 5),
        (".alloc pinned at 0xc18000 {\n    .dw 1, 2, 3\n}\n", "pinned.__size", 6),
        (".reserve buf 0x40 in wram\n", "buf.__size", 0x40),
        (".struct Pt {\n    word x\n    word y\n}\n.reserve pt as Pt in wram\n", "pt.__size", 4),
        (".alloc empty in code {\n}\n", "empty.__size", 0),
    ],
    ids=["pooled", "pinned", "reserve", "typed reserve", "empty"],
)
def test_a_named_alloc_publishes_its_size(src: str, name: str, size: int) -> None:
    assert _sizes(src)[name] == size


def test_a_nameless_alloc_publishes_no_size() -> None:
    sizes = _sizes(".alloc at 0xc18000 {\n    .db 1\n}\n")
    assert not [name for name in sizes if name.endswith(".__size")]


def test_the_size_is_usable_in_the_module() -> None:
    src = ".reserve buf 0x40 in wram\n.alloc clear in code {\n    ldx.w #buf.__size - 1\n}\n"
    with tempfile.TemporaryDirectory() as tmp:
        code = b"".join(section.code for section in _link(src, tmp).sections)
    assert code == b"\xa2\x3f\x00"


def _build(root: Path, files: dict[str, str]) -> BuildResult:
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    return build_with_imports(root / "main.s", root / "out.ips", module_paths=[root], output_dir=root / "obj")


def _ips_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    out, i = b"", 5
    while data[i : i + 3] != b"EOF":
        size = int.from_bytes(data[i + 3 : i + 5], "big")
        out += data[i + 5 : i + 5 + size]
        i += 5 + size
    return out


def test_an_importer_reads_the_size_of_another_module_s_alloc(tmp_path: Path) -> None:
    lib = _PREAMBLE + ".alloc blob in code {\n    .db 1, 2, 3, 4, 5\n}\n"
    main = '.import "lib"\n.alloc user at 0xc1f000 {\n    lda.w #blob.__size\n}\n'
    result = _build(tmp_path, {"lib.s": lib, "main.s": main})
    assert result.exit_code == 0, result.diagnostics
    assert b"\xa9\x05\x00" in _ips_bytes(tmp_path / "out.ips")
