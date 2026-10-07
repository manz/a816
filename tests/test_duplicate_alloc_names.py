"""Two modules declaring the same alloc name is a duplicate symbol, not an overlap of two pools."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from a816.module_builder import BuildResult, build_with_imports
from tests.test_reserve import _PREAMBLE


def _build(root: Path, files: dict[str, str], caplog: pytest.LogCaptureFixture) -> BuildResult:
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        return build_with_imports(root / "main.s", root / "out.ips", module_paths=[root], output_dir=root / "obj")


_TWO_CODES = {
    "pre.s": _PREAMBLE,
    "a.s": '.import "pre"\n.alloc code in code {\n    nop\n}\n',
    "b.s": '.import "pre"\n.alloc code in code {\n    rts\n}\n',
    "main.s": '.import "a"\n.import "b"\n',
}


def test_two_modules_reusing_an_alloc_name_is_e0400(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    result = _build(tmp_path, _TWO_CODES, caplog)
    assert (result.exit_code, "E0400" in result.diagnostics[0], "E0408" in result.diagnostics[0]) == (1, True, False)


@pytest.mark.parametrize("module", ["a.s:2", "b.s:2"])
def test_the_error_names_both_declarations(tmp_path: Path, caplog: pytest.LogCaptureFixture, module: str) -> None:
    result = _build(tmp_path, _TWO_CODES, caplog)
    assert module in result.diagnostics[0]


def test_one_module_imported_twice_still_shares_its_alloc(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    files = {
        "pre.s": _PREAMBLE,
        "lib.s": '.import "pre"\n.alloc helper in code {\n    rts\n}\n',
        "a.s": '.import "lib"\n.alloc a_code in code {\n    jsr.w helper\n}\n',
        "b.s": '.import "lib"\n.alloc b_code in code {\n    jsr.w helper\n}\n',
        "main.s": '.import "a"\n.import "b"\n',
    }
    result = _build(tmp_path, files, caplog)
    assert result.exit_code == 0, result.diagnostics
