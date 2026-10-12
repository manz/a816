"""W0001 reaches every reference to a path-derived `.incbin` name.

ff4 had references in modules that never imported the blob's module: the
compile-time check sees only the unit and its imports, so they bound at
link with no warning and would have become link errors in 1.2. And a
module served from the build cache printed none of its warnings, so a
warm build hid the deprecation.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from a816.module_builder import build_with_imports

ASSETS = '.alloc blobs at 0x018000 {\n    .incbin "data/x.bin"\n    .db 0\n}\n'
LINK_ONLY = ".extern data_x_bin\n.alloc code at 0x008000 {\n    lda.l data_x_bin\n}\n"
IMPORTED = '.import "assets"\n.alloc code at 0x008000 {\n    lda.l data_x_bin\n}\n'


def _w0001(tmp_path: Path, caplog: pytest.LogCaptureFixture, code: str) -> list[str]:
    """Build main.s (imports assets + code); the W0001 sites it reported."""
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "x.bin").write_bytes(b"\x01\x02")
    (tmp_path / "assets.s").write_text(ASSETS, encoding="utf-8")
    (tmp_path / "code.s").write_text(code, encoding="utf-8")
    (tmp_path / "main.s").write_text('.import "assets"\n.import "code"\n', encoding="utf-8")
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        result = build_with_imports(
            tmp_path / "main.s",
            tmp_path / "out.sfc",
            module_paths=[tmp_path],
            include_paths=[tmp_path],
            output_dir=tmp_path / "obj",
            output_format="sfc",
        )
    assert result.exit_code == 0, result.diagnostics
    text = re.sub(r"\x1b\[[0-9;]*m", "", caplog.text)
    return re.findall(r"warning\[W0001\].*\n\s*--> (\S+)", text)


def test_a_reference_bound_only_at_link_warns(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    sites = _w0001(tmp_path, caplog, LINK_ONLY)

    assert [Path(site).name for site in sites] == ["code.s:3:11"]


def test_an_imported_reference_warns_once(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    sites = _w0001(tmp_path, caplog, IMPORTED)

    assert [Path(site).name for site in sites] == ["code.s:3:11"]


@pytest.mark.parametrize("code", [LINK_ONLY, IMPORTED])
def test_a_warm_build_repeats_the_cold_warnings(tmp_path: Path, caplog: pytest.LogCaptureFixture, code: str) -> None:
    cold = _w0001(tmp_path, caplog, code)

    assert _w0001(tmp_path, caplog, code) == cold


def test_a_label_reference_does_not_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    labelled = '.alloc blobs at 0x018000 {\nblob:\n    .incbin "data/x.bin"\n}\n'
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "x.bin").write_bytes(b"\x01\x02")
    (tmp_path / "assets.s").write_text(labelled, encoding="utf-8")
    code = ".extern blob\n.alloc code at 0x008000 {\n    lda.l blob\n}\n"

    assert _w0001_without_assets(tmp_path, caplog, code) == []


def _w0001_without_assets(tmp_path: Path, caplog: pytest.LogCaptureFixture, code: str) -> list[str]:
    (tmp_path / "code.s").write_text(code, encoding="utf-8")
    (tmp_path / "main.s").write_text('.import "assets"\n.import "code"\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        result = build_with_imports(
            tmp_path / "main.s",
            tmp_path / "out.sfc",
            module_paths=[tmp_path],
            include_paths=[tmp_path],
            output_dir=tmp_path / "obj",
            output_format="sfc",
        )
    assert result.exit_code == 0, result.diagnostics
    return re.findall(r"W0001", caplog.text)
