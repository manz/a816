"""W0001: a reference to a name `.incbin` derived from its file path.

`.incbin "f.bin"` binds `f_bin` and `f_bin__size`, named after the file;
the alloc that holds the blob, or a label, is what code should name. The
names still work (the build succeeds) until they go in 1.2.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.module_builder import build_with_imports

ALONE = '.alloc font at 0x008000 {\n    .incbin "f.bin"\n}\n'
SHARED = '.alloc at 0x008000 {\n    .incbin "f.bin"\n    .db 0\n}\n'


def _build(tmp_path: Path, caplog: pytest.LogCaptureFixture, main: str, module: str | None = None) -> str:
    (tmp_path / "f.bin").write_bytes(b"ABCD")
    if module is not None:
        (tmp_path / "m.s").write_text(module, encoding="utf-8")
    (tmp_path / "main.s").write_text(main, encoding="utf-8")
    result = build_with_imports(
        tmp_path / "main.s",
        tmp_path / "out.ips",
        module_paths=[tmp_path],
        include_paths=[tmp_path],
        output_dir=tmp_path / "obj",
    )
    assert result.exit_code == 0
    return caplog.text


def test_a_path_name_reference_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    text = _build(tmp_path, caplog, ALONE + ".alloc code at 0x009000 {\n    lda.l f_bin\n}\n")

    assert "warning[W0001]: `f_bin` is named after the asset path 'f.bin'" in text


def test_the_hint_names_the_alloc(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    text = _build(tmp_path, caplog, ALONE + ".alloc code at 0x009000 {\n    lda.l f_bin\n}\n")

    assert "write `font`" in text


def test_a_size_reference_is_pointed_at_sizeof(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    text = _build(tmp_path, caplog, ALONE + ".alloc code at 0x009000 {\n    lda #f_bin__size\n}\n")

    assert "write `sizeof(font)`" in text


def test_a_blob_sharing_its_block_asks_for_a_label(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    text = _build(tmp_path, caplog, SHARED + ".alloc code at 0x009000 {\n    lda.l f_bin\n}\n")

    assert 'put a label before `.incbin "f.bin"`' in text


def test_a_reference_to_an_imported_blob_warns_in_the_importer(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    text = _build(tmp_path, caplog, '.import "m"\n.alloc code at 0x009000 {\n    lda.l f_bin\n}\n', ALONE)

    assert "main.s:3:11" in text.split("W0001", 1)[1]


def test_an_unreferenced_blob_is_quiet(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert "W0001" not in _build(tmp_path, caplog, ALONE)


def test_the_alloc_name_itself_is_quiet(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    text = _build(tmp_path, caplog, ALONE + ".alloc code at 0x009000 {\n    lda.l font\n    lda #sizeof(font)\n}\n")

    assert "W0001" not in text


TWICE = '.alloc at 0x008000 {\n    .incbin "f.bin"\n}\n.alloc at 0x009000 {\n    .incbin "f_bin"\n}\n'


def _failed(tmp_path: Path, caplog: pytest.LogCaptureFixture, main: str) -> str:
    (tmp_path / "f.bin").write_bytes(b"AB")
    (tmp_path / "f_bin").write_bytes(b"CDEF")
    (tmp_path / "main.s").write_text(main, encoding="utf-8")
    result = build_with_imports(
        tmp_path / "main.s", tmp_path / "out.ips", include_paths=[tmp_path], output_dir=tmp_path / "obj"
    )
    assert result.exit_code != 0
    return caplog.text


def test_a_reference_to_a_name_two_blobs_bind_is_an_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """`f.bin` and `f_bin` both bind `f_bin`; the reference read the first one's size."""
    text = _failed(tmp_path, caplog, TWICE + ".alloc code at 0x00A000 {\n    lda #f_bin__size\n}\n")

    assert "error[E0347]" in text


def test_two_blobs_binding_one_name_unreferenced_still_build(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """dq6 includes a file twice without naming it: that keeps building."""
    (tmp_path / "f.bin").write_bytes(b"AB")
    (tmp_path / "f_bin").write_bytes(b"CDEF")

    assert "E0347" not in _build(tmp_path, caplog, TWICE)


def test_an_importer_of_a_private_alloc_is_told_to_make_it_public(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """ff4's `_items_unleashed`: `write _items_unleashed` would be E0200 in the importer."""
    module = '.alloc _font at 0x008000 {\n    .incbin "f.bin"\n}\n'
    text = _build(tmp_path, caplog, '.import "m"\n.alloc code at 0x009000 {\n    lda.l f_bin\n}\n', module)

    assert "rename it `font` and use `font`" in text


def test_a_name_an_alloc_also_binds_is_quiet(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    (tmp_path / "f_bin").write_bytes(b"AB")
    main = '.alloc f_bin at 0x008000 {\n    .incbin "f_bin"\n}\n.alloc code at 0x009000 {\n    lda.l f_bin\n}\n'

    assert "W0001" not in _build(tmp_path, caplog, main)
