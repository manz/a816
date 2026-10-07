"""`sizeof` inside an alias that reaches the linker: the alias still links and lands right."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from a816.module_builder import BuildResult, build_with_imports
from tests.test_reserve import _PREAMBLE, _link

_DATA = (
    _PREAMBLE
    + ".alloc blob in code {\n    .db 1, 2, 3, 4, 5, 6, 7, 8\n}\n"
    + "LUT_BYTES = 2\n"
    + "blob_lut = blob + sizeof(blob) - LUT_BYTES\n"
    + "blob_body_size = sizeof(blob) - LUT_BYTES\n"
)


def _build(root: Path, main: str) -> BuildResult:
    (root / "data.s").write_text(_DATA, encoding="utf-8")
    (root / "main.s").write_text(main, encoding="utf-8")
    return build_with_imports(root / "main.s", root / "out.ips", module_paths=[root], output_dir=root / "obj")


def _ips_tail(path: Path, size: int) -> bytes:
    data = path.read_bytes()
    out, i = b"", 5
    while data[i : i + 3] != b"EOF":
        length = int.from_bytes(data[i + 3 : i + 5], "big")
        out += data[i + 5 : i + 5 + length]
        i += 5 + length
    return out[-size:]


_MACRO = ".macro load(n) {\n    lda.w #n\n}\n"


_BLOB = ".alloc blob in code {\n    .db 1, 2, 3, 4, 5, 6, 7, 8\n}\n"


@pytest.mark.parametrize(
    ("src", "code"),
    [
        (_BLOB + _MACRO + ".alloc user in code {\n    load(sizeof(blob))\n}\n", b"\xa9\x08\x00"),
        (_BLOB + '.alloc user in code {\n    nop\n}\n.assert sizeof(blob) == 8, "blob is 8"\n', b"\xea"),
    ],
    ids=["macro argument", "assert"],
)
def test_sizeof_of_an_alloc_in_its_own_module_before_it_is_measured(src: str, code: bytes) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        sections = _link(src, tmp).sections
    assert b"".join(section.code for section in sections).endswith(code)


def test_sizeof_of_an_imported_alloc_in_a_macro_argument(tmp_path: Path) -> None:
    result = _build(tmp_path, '.import "data"\n' + _MACRO + ".alloc user at 0xc1f000 {\n    load(sizeof(blob))\n}\n")
    assert result.exit_code == 0, result.diagnostics
    assert _ips_tail(tmp_path / "out.ips", 3) == b"\xa9\x08\x00"


@pytest.mark.parametrize(
    ("use", "expected"),
    [
        (
            "    lda.l blob_lut, x\n",
            lambda blob: bytes([0xBF, (blob + 6) & 0xFF, (blob + 6) >> 8 & 0xFF, (blob + 6) >> 16]),
        ),
        ("    lda.w #blob_body_size\n", lambda blob: b"\xa9\x06\x00"),
    ],
    ids=["address alias", "size alias"],
)
def test_an_importer_uses_an_alias_built_on_sizeof(tmp_path: Path, use: str, expected: object) -> None:
    result = _build(tmp_path, '.import "data"\n.alloc user at 0xc1f000 {\n' + use + "}\n")
    assert result.exit_code == 0, result.diagnostics
    blob = result.symbol_map["blob"]
    assert callable(expected)
    want = expected(blob)
    assert _ips_tail(tmp_path / "out.ips", len(want)) == want


@pytest.mark.parametrize(
    ("name", "spelled"),
    [("vwf_font.__size", "sizeof(vwf_font)"), ("LEAK", "LEAK")],
)
def test_the_unimported_use_warning_spells_sizes_as_sizeof(name: str, spelled: str) -> None:
    from a816.module_builder import _spelled

    assert _spelled(name) == spelled
