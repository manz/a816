"""A whole-project fluff run leaves out what git ignores and what
`exclude` names.

Bahamut Lagoon (1.1.1): a bare `a816 check` reported 531 hits, 530 of them
from a gitignored `debug_src/` and untracked root scratch (`blold.s`), old
syntax that no longer parses. Only `bl.s` was a real source.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from a816.config import load_a816_toml
from a816.exceptions import A816ConfigError
from a816.fluff.project import project_sources

CLEAN = '"""Module."""\n'
GIT = shutil.which("git")


def _project(root: Path, toml: str = "") -> Path:
    (root / "a816.toml").write_text(f'entrypoint = "bl.s"\nmodule-paths = ["src"]\n{toml}', encoding="utf-8")
    for name in ("bl.s", "src/a.s", "debug_src/old.s", "blold.s", "scratch/try.s"):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(CLEAN, encoding="utf-8")
    return root.resolve()


def _sources(root: Path) -> list[str]:
    return [path.relative_to(root).as_posix() for path in project_sources(root / "a816.toml")]


def _git_init(root: Path, ignore: str) -> None:
    assert GIT is not None
    subprocess.run([GIT, "init", "-q"], cwd=root, check=True)
    (root / ".gitignore").write_text(ignore, encoding="utf-8")


@pytest.mark.skipif(GIT is None, reason="needs git")
def test_a_gitignored_directory_is_left_out(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _git_init(root, "debug_src/\n")

    assert "debug_src/old.s" not in _sources(root)


@pytest.mark.skipif(GIT is None, reason="needs git")
def test_untracked_files_git_does_not_ignore_still_count(tmp_path: Path) -> None:
    root = _project(tmp_path)
    _git_init(root, "debug_src/\n")

    assert _sources(root) == ["bl.s", "blold.s", "scratch/try.s", "src/a.s"]


def test_exclude_leaves_out_files_and_directories(tmp_path: Path) -> None:
    root = _project(tmp_path, 'exclude = ["blold.s", "scratch", "debug_*"]\n')

    assert _sources(root) == ["bl.s", "src/a.s"]


def test_exclude_takes_a_glob_over_nested_paths(tmp_path: Path) -> None:
    root = _project(tmp_path, 'exclude = ["src/*.s"]\n')

    assert "src/a.s" not in _sources(root)


def test_outside_a_work_tree_every_source_counts(tmp_path: Path) -> None:
    root = _project(tmp_path)

    assert _sources(root) == ["bl.s", "blold.s", "debug_src/old.s", "scratch/try.s", "src/a.s"]


def test_exclude_must_be_a_list_of_strings(tmp_path: Path) -> None:
    (tmp_path / "a816.toml").write_text('exclude = "scratch"\n', encoding="utf-8")

    with pytest.raises(A816ConfigError) as error:
        load_a816_toml(tmp_path / "a816.toml")

    assert error.value.code.code == "E0501"
