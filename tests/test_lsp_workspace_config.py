"""`WorkspaceIndex` reads `a816.toml` through the shared `a816.config` loader."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from a816.lsp.workspace import WorkspaceIndex

_PRAGMA_ENTRY = ";! a816-lsp entrypoint\nnop\n"


def _prepared(root: Path, toml: str) -> WorkspaceIndex:
    (root / "a816.toml").write_text(toml, encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "main.s").write_text("nop\n", encoding="utf-8")
    index = WorkspaceIndex(root)
    index.prepare()
    return index


def test_config_entrypoint_is_used(tmp_path: Path) -> None:
    index = _prepared(tmp_path, 'entrypoint = "src/main.s"\n')
    assert index.entrypoint == (tmp_path / "src" / "main.s").resolve()


def test_missing_config_entrypoint_falls_back(tmp_path: Path) -> None:
    index = _prepared(tmp_path, 'entrypoint = "src/absent.s"\n')
    assert index.entrypoint == (tmp_path / "src" / "main.s").resolve()


def test_config_search_paths_are_loaded(tmp_path: Path) -> None:
    index = _prepared(tmp_path, 'include-paths = ["inc"]\nmodule-paths = ["src"]\n')
    assert (index.include_paths, index.module_paths) == ([(tmp_path / "inc").resolve()], [(tmp_path / "src").resolve()])


def test_config_search_paths_apply_alongside_a_pragma(tmp_path: Path) -> None:
    (tmp_path / "entry.s").write_text(_PRAGMA_ENTRY, encoding="utf-8")
    index = _prepared(tmp_path, 'include-paths = ["inc"]\n')
    assert index.include_paths == [(tmp_path / "inc").resolve()]


def test_config_is_found_above_the_workspace_root(tmp_path: Path) -> None:
    (tmp_path / "a816.toml").write_text('include-paths = ["inc"]\n', encoding="utf-8")
    nested = tmp_path / "nested"
    nested.mkdir()
    index = WorkspaceIndex(nested)
    index.prepare()
    assert index.include_paths == [(tmp_path / "inc").resolve()]


def test_invalid_config_does_not_break_indexing(tmp_path: Path) -> None:
    index = _prepared(tmp_path, 'mapper = "exhirom"\n')
    assert index.entrypoint == (tmp_path / "src" / "main.s").resolve()


def test_invalid_config_is_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="a816.lsp.workspace"):
        _prepared(tmp_path, 'mapper = "exhirom"\n')
    assert "E0504" in caplog.text
