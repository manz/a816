"""`.incbin` no longer names its blob after its file path (1.3).

`.incbin "f.bin"` bound `f_bin` and `f_bin__size` until 1.3. A leftover
reference is undefined; the error says what the name was and what to
write instead, at compile time and, for a name only the linker meets, at
link time.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from a816.module_builder import build_with_imports

ALONE = '.alloc font at 0x008000 {\n    .incbin "f.bin"\n}\n'
SHARED = '.alloc at 0x008000 {\n    .incbin "f.bin"\n    .db 0\n}\n'
TWICE = '.alloc at 0x008000 {\n    .incbin "f.bin"\n}\n.alloc at 0x009000 {\n    .incbin "f_bin"\n}\n'


def _build(tmp_path: Path, caplog: pytest.LogCaptureFixture, main: str, module: str | None = None) -> tuple[int, str]:
    (tmp_path / "f.bin").write_bytes(b"ABCD")
    (tmp_path / "f_bin").write_bytes(b"EF")
    if module is not None:
        (tmp_path / "m.s").write_text(module, encoding="utf-8")
    (tmp_path / "main.s").write_text(main, encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = build_with_imports(
            tmp_path / "main.s",
            tmp_path / "out.ips",
            module_paths=[tmp_path],
            include_paths=[tmp_path],
            output_dir=tmp_path / "obj",
        )
    return result.exit_code, re.sub(r"\x1b\[[0-9;]*m", "", caplog.text)


def _error(tmp_path: Path, caplog: pytest.LogCaptureFixture, main: str, module: str | None = None) -> str:
    exit_code, text = _build(tmp_path, caplog, main, module)
    assert exit_code != 0
    return text


def test_a_path_name_reference_is_undefined(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    text = _error(tmp_path, caplog, ALONE + ".alloc code at 0x009000 {\n    lda.l f_bin\n}\n")

    assert "error[E0200]: `f_bin` is not defined" in text


def test_the_error_says_what_the_name_was(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    text = _error(tmp_path, caplog, ALONE + ".alloc code at 0x009000 {\n    lda.l f_bin\n}\n")

    assert '`.incbin "f.bin"` named its blob after its path until a816 1.3' in text


def test_the_hint_names_the_alloc(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    text = _error(tmp_path, caplog, ALONE + ".alloc code at 0x009000 {\n    lda.l f_bin\n}\n")

    assert "write `font`" in text


def test_a_size_reference_is_pointed_at_sizeof(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    text = _error(tmp_path, caplog, ALONE + ".alloc code at 0x009000 {\n    lda #f_bin__size\n}\n")

    assert "write `sizeof(font)`" in text


def test_a_blob_sharing_its_block_asks_for_a_label(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    text = _error(tmp_path, caplog, SHARED + ".alloc code at 0x009000 {\n    lda.l f_bin\n}\n")

    assert 'put a label before `.incbin "f.bin"`' in text


def test_an_imported_blob_is_reported_in_the_importer(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    text = _error(tmp_path, caplog, '.import "m"\n.alloc code at 0x009000 {\n    lda.l f_bin\n}\n', ALONE)

    assert "main.s:3:11" in text


def test_an_importer_of_a_private_alloc_is_told_to_make_it_public(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    module = '.alloc _font at 0x008000 {\n    .incbin "f.bin"\n}\n'
    text = _error(tmp_path, caplog, '.import "m"\n.alloc code at 0x009000 {\n    lda.l f_bin\n}\n', module)

    assert "rename it `font` and use `font`" in text


def test_a_name_two_blobs_bound_asks_for_labels(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    text = _error(tmp_path, caplog, TWICE + ".alloc code at 0x00A000 {\n    lda #f_bin__size\n}\n")

    assert "label each blob" in text


def test_an_unreferenced_blob_builds(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert _build(tmp_path, caplog, ALONE + TWICE.replace("0x008000", "0x00A000"))[0] == 0


def test_the_alloc_name_builds(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    main = ALONE + ".alloc code at 0x009000 {\n    lda.l font\n    lda #sizeof(font)\n}\n"

    assert _build(tmp_path, caplog, main) == (0, "")


def test_a_name_an_alloc_also_binds_builds(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    main = '.alloc f_bin at 0x008000 {\n    .incbin "f_bin"\n}\n.alloc code at 0x009000 {\n    lda.l f_bin\n}\n'

    assert _build(tmp_path, caplog, main)[0] == 0


ASSETS = '.alloc blobs at 0x018000 {\n    .incbin "data/x.bin"\n    .db 0\n}\n'


def _link_error(tmp_path: Path, caplog: pytest.LogCaptureFixture, code: str) -> str:
    """Build main.s importing assets and code; code names the blob only through `.extern`."""
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "x.bin").write_bytes(b"\x01\x02")
    (tmp_path / "assets.s").write_text(ASSETS, encoding="utf-8")
    (tmp_path / "code.s").write_text(code, encoding="utf-8")
    return _error(tmp_path, caplog, '.import "assets"\n.import "code"\n')


def test_a_name_only_the_linker_meets_says_what_it_was(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    code = ".extern data_x_bin\n.alloc code at 0x008000 {\n    lda.l data_x_bin\n}\n"

    assert '`.incbin "data/x.bin"` named its blob after its path' in _link_error(tmp_path, caplog, code)


def test_several_unresolved_names_each_say_what_they_were(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    code = (
        ".extern data_x_bin\n.extern data_x_bin__size\n.extern elsewhere\n"
        ".alloc code at 0x008000 {\n    lda.l data_x_bin\n    lda #data_x_bin__size\n    lda.l elsewhere\n}\n"
    )
    text = _link_error(tmp_path, caplog, code)

    assert '`data_x_bin__size`: `.incbin "data/x.bin"` named its blob' in text
