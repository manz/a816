"""`check` and `format` find an `.include` through the a816.toml include-paths.

The build did since #241, but the formatter parsed without them, so a file
under an include path (Feda's build/gen, dq6's layout.i) failed `check` and
`format --check` with `[Errno 2] No such file or directory`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.fluff import fluff_main


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "a816.toml").write_text('include-paths = ["gen"]\nmodule-paths = ["src"]\n', encoding="utf-8")
    (tmp_path / "gen").mkdir()
    (tmp_path / "gen" / "data.s").write_text("VALUE = 0x42\n", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "menu.s").write_text('"""Menu."""\n.include "data.s"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize("command", [["check"], ["format", "--check"]], ids=["check", "format-check"])
def test_an_include_under_an_include_path_is_found(
    project: Path, command: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    fluff_main([*command, "src/menu.s"])

    assert "No such file" not in capsys.readouterr().err


@pytest.mark.parametrize("command", [["check"], ["format", "--check"]], ids=["check", "format-check"])
def test_an_include_under_an_include_path_passes(project: Path, command: list[str]) -> None:
    assert fluff_main([*command, "src/menu.s"]) == 0


def test_an_importer_of_such_a_module_passes_check(project: Path) -> None:
    """The struct-name scan re-parses imported modules; it searched no include paths."""
    (project / "src" / "main.s").write_text('"""Main."""\n.import "menu"\n', encoding="utf-8")

    assert fluff_main(["check", "src/main.s"]) == 0


def test_format_names_the_include_paths_it_searched(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """dq6: format's E0500 hint listed only the file's own directory."""
    (project / "gen" / "data.s").unlink()

    fluff_main(["format", "--check", "src/menu.s"])

    assert "searched src, gen" in capsys.readouterr().err
