"""`a816 check` / `format` / `fix` with no paths take the project's sources.

dq6 (rc1): `make check` had to spell out the source list, and a bare
`a816 check` from the project root was a usage error.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.fluff import fluff_main
from a816.fluff.project import project_sources

CLEAN = '"""Module."""\n'
UNFORMATTED = '"""Module."""\nstart:\nlda #0\n'


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """BL's shape: the entrypoint at the root, modules under src/, and
    generated sources under build/ and the obj dir."""
    (tmp_path / "a816.toml").write_text('entrypoint = "bl.s"\nmodule-paths = ["src"]\n', encoding="utf-8")
    for name in ("bl.s", "src/a.s", "src/b.i", "build/gen.s", "obj/x.s", ".cache/y.s"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(CLEAN, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path.resolve()


def test_the_project_sources_skip_generated_dirs(project: Path) -> None:
    sources = [path.relative_to(project).as_posix() for path in project_sources(project / "a816.toml")]

    assert sources == ["bl.s", "src/a.s", "src/b.i"]


def test_a_module_path_outside_the_entrypoint_dir_is_walked(tmp_path: Path) -> None:
    tmp_path = tmp_path.resolve()
    (tmp_path / "a816.toml").write_text('entrypoint = "game/main.s"\nmodule-paths = ["lib"]\n', encoding="utf-8")
    for name in ("game/main.s", "lib/util.s", "tools/gen.s"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(CLEAN, encoding="utf-8")

    sources = [path.relative_to(tmp_path).as_posix() for path in project_sources(tmp_path / "a816.toml")]

    assert sources == ["game/main.s", "lib/util.s"]


def test_without_an_entrypoint_the_project_root_is_walked(tmp_path: Path) -> None:
    tmp_path = tmp_path.resolve()
    (tmp_path / "a816.toml").write_text("", encoding="utf-8")
    (tmp_path / "main.s").write_text(CLEAN, encoding="utf-8")

    assert project_sources(tmp_path / "a816.toml") == [tmp_path / "main.s"]


def test_a_bare_check_lints_the_project(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / "src" / "a.s").write_text("start:\n    rts\n", encoding="utf-8")

    code = fluff_main(["check"])

    assert (code, capsys.readouterr().out.startswith("src/a.s:")) == (1, True)


def test_a_bare_check_ignores_generated_sources(project: Path) -> None:
    (project / "build" / "gen.s").write_text("start:\n    rts\n", encoding="utf-8")

    assert fluff_main(["check"]) == 0


def test_a_bare_format_check_reports_the_project(project: Path) -> None:
    (project / "bl.s").write_text(UNFORMATTED, encoding="utf-8")

    assert fluff_main(["format", "--check"]) == 1


def test_a_bare_fix_runs_over_the_project(project: Path) -> None:
    assert fluff_main(["fix", "--check"]) == 0


def test_a_bare_check_from_a_subdirectory_finds_the_toml(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(project / "src")

    assert fluff_main(["check"]) == 0


def test_a_bare_check_outside_a_project_is_a_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exit_info:
        fluff_main(["check"])

    assert (exit_info.value.code, "a816.toml" in capsys.readouterr().err) == (2, True)
