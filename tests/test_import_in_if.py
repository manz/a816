"""An `.import` inside `.if` is an error: it was imported whatever the condition.

Imports are lifted to the build's module list from the source, before any
condition is evaluated, so `.if FLAG { .import "m" }` linked `m` with FLAG 0,
1 or undefined (ff4, rc3: 24 such imports, one a debug patch).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from a816.module_builder import build_with_imports

MODULE = ".alloc m_code at 0x009000 {\n    .db 0xAB\n}\n"


def _log(tmp_path: Path, caplog: pytest.LogCaptureFixture, main: str) -> tuple[int, str]:
    (tmp_path / "m.s").write_text(MODULE, encoding="utf-8")
    (tmp_path / "main.s").write_text(main, encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        result = build_with_imports(
            tmp_path / "main.s", tmp_path / "o.ips", module_paths=[tmp_path], output_dir=tmp_path / "obj"
        )
    return result.exit_code, caplog.text


@pytest.mark.parametrize("flag", ["FLAG = 0\n", "FLAG = 1\n", ""], ids=["false", "true", "undefined"])
def test_an_import_inside_an_if_is_an_error(tmp_path: Path, caplog: pytest.LogCaptureFixture, flag: str) -> None:
    code, log = _log(tmp_path, caplog, flag + '.if FLAG {\n.import "m"\n}\n')

    assert (code != 0, "error[E0311]" in log) == (True, True)


def test_an_import_in_the_else_branch_is_an_error_too(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    code, log = _log(tmp_path, caplog, 'FLAG = 1\n.if FLAG {\n} else {\n.import "m"\n}\n')

    assert (code != 0, "error[E0311]" in log) == (True, True)


def test_a_nested_import_is_found(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    code, log = _log(tmp_path, caplog, 'A = 1\nB = 0\n.if A {\n.if B {\n.import "m"\n}\n}\n')

    assert (code != 0, "error[E0311]" in log) == (True, True)


def test_the_hint_says_where_to_put_the_condition(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _, log = _log(tmp_path, caplog, 'FLAG = 0\n.if FLAG {\n.import "m"\n}\n')

    assert "inside the module" in log


def test_an_if_without_imports_still_builds(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    code, _ = _log(tmp_path, caplog, '.import "m"\nFLAG = 0\n.if FLAG {\nX = 1\n}\n')

    assert code == 0
