"""A failed compile leaves no object for the build cache to reuse once the source is fixed."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from a816.module_builder import BuildResult, build_with_imports
from tests.test_reserve import _PREAMBLE

_LIB = _PREAMBLE + ".alloc helper in code {\n    rts\n}\n"
_MAIN = '.import "lib"\n.alloc user at 0xc1f000 {\n    jsr.w helper\n}\n'


def _build(root: Path) -> BuildResult:
    return build_with_imports(root / "main.s", root / "out.ips", module_paths=[root], output_dir=root / "obj")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "lib.s").write_text(_LIB, encoding="utf-8")
    (tmp_path / "main.s").write_text(_MAIN, encoding="utf-8")
    assert _build(tmp_path).exit_code == 0
    return tmp_path


def _break_lib(root: Path, caplog: pytest.LogCaptureFixture) -> None:
    (root / "lib.s").write_text(_LIB + "    lda #undefined_name\n", encoding="utf-8")
    with caplog.at_level(logging.CRITICAL):
        assert _build(root).exit_code != 0


def test_a_failed_compile_leaves_no_object(project: Path, caplog: pytest.LogCaptureFixture) -> None:
    _break_lib(project, caplog)
    assert sorted(path.name for path in (project / "obj").glob("lib.*")) == []


def test_fixing_the_source_rebuilds_the_module(project: Path, caplog: pytest.LogCaptureFixture) -> None:
    _break_lib(project, caplog)
    (project / "lib.s").write_text(_LIB, encoding="utf-8")
    result = _build(project)
    assert (result.exit_code, "helper" in result.symbol_map) == (0, True), result.diagnostics
