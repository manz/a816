"""`[lint.per-file-ignores]` in a816.toml: rule codes dropped in the files a glob matches."""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.config import load_a816_toml
from a816.exceptions import A816ConfigError
from a816.fluff.cli import fluff_main
from a816.fluff.selection import RuleSelection
from a816.lsp.document import A816Document

_LONG = '"""Module."""\n; ' + "x" * 130 + "\n"  # E501


def _project(tmp_path: Path, lint: str, source: str = "src/disasm/bank_c0.s") -> Path:
    (tmp_path / "a816.toml").write_text(f'entrypoint = "src/main.s"\n{lint}', encoding="utf-8")
    path = tmp_path / source
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_LONG, encoding="utf-8")
    return path


def _selection(tmp_path: Path) -> RuleSelection:
    return RuleSelection.of(load_a816_toml(tmp_path / "a816.toml"))


def test_the_table_loads(tmp_path: Path) -> None:
    _project(tmp_path, '[lint.per-file-ignores]\n"src/disasm/*.s" = ["n801", "E5"]\n')
    config = load_a816_toml(tmp_path / "a816.toml")
    assert config is not None
    assert config.lint_per_file_ignores == (("src/disasm/*.s", ("N801", "E5")),)


def test_a_matching_file_drops_the_code(tmp_path: Path) -> None:
    source = _project(tmp_path, '[lint.per-file-ignores]\n"src/disasm/*.s" = ["E5"]\n')
    assert not _selection(tmp_path).allows("E501", source)


def test_another_file_keeps_the_code(tmp_path: Path) -> None:
    _project(tmp_path, '[lint.per-file-ignores]\n"src/disasm/*.s" = ["E5"]\n')
    assert _selection(tmp_path).allows("E501", tmp_path / "src" / "main.s")


def test_a_directory_pattern_covers_its_files(tmp_path: Path) -> None:
    source = _project(tmp_path, '[lint.per-file-ignores]\n"src/disasm" = ["E501"]\n')
    assert not _selection(tmp_path).allows("E501", source)


def test_other_codes_still_run_in_a_matching_file(tmp_path: Path) -> None:
    source = _project(tmp_path, '[lint.per-file-ignores]\n"src/disasm/*.s" = ["N801"]\n')
    assert _selection(tmp_path).allows("E501", source)


def test_without_a_path_only_the_lists_apply(tmp_path: Path) -> None:
    _project(tmp_path, '[lint.per-file-ignores]\n"src/disasm/*.s" = ["E501"]\n')
    assert _selection(tmp_path).allows("E501")


def test_a_relative_path_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, '[lint.per-file-ignores]\n"src/disasm/*.s" = ["E501"]\n')
    monkeypatch.chdir(tmp_path)
    assert not _selection(tmp_path).allows("E501", Path("src/disasm/bank_c0.s"))


@pytest.mark.parametrize(
    "lint",
    [
        '[lint]\nper-file-ignores = ["N801"]\n',
        '[lint.per-file-ignores]\n"src/*.s" = "N801"\n',
        '[lint.per-file-ignores]\n"src/*.s" = [1]\n',
    ],
)
def test_a_malformed_table_is_refused(tmp_path: Path, lint: str) -> None:
    _project(tmp_path, lint)
    with pytest.raises(A816ConfigError, match=r"per-file-ignores"):
        load_a816_toml(tmp_path / "a816.toml")


def test_an_unknown_code_is_refused(tmp_path: Path) -> None:
    _project(tmp_path, '[lint.per-file-ignores]\n"src/*.s" = ["N999"]\n')
    with pytest.raises(A816ConfigError, match="names no rule: N999"):
        _selection(tmp_path)


def test_check_honours_it(tmp_path: Path) -> None:
    source = _project(tmp_path, '[lint.per-file-ignores]\n"src/disasm/*.s" = ["E501"]\n')
    assert fluff_main(["check", str(source)]) == 0


def test_check_still_reports_other_files(tmp_path: Path) -> None:
    source = _project(tmp_path, '[lint.per-file-ignores]\n"src/disasm/*.s" = ["E501"]\n', "src/main.s")
    assert fluff_main(["check", str(source)]) == 1


def test_fix_honours_it(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _project(tmp_path, '[lint.per-file-ignores]\n"src/disasm/*.s" = ["E501"]\n')
    fluff_main(["fix", "--check", str(source)])
    assert "E501" not in capsys.readouterr().out


def test_the_lsp_honours_it(tmp_path: Path) -> None:
    source = _project(tmp_path, '[lint.per-file-ignores]\n"src/disasm/*.s" = ["E501"]\n')
    document = A816Document(source.as_uri(), _LONG)
    assert all(d.code != "E501" for d in document.diagnostics)
