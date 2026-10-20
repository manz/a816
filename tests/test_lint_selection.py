"""`[lint]` select/ignore in a816.toml and `a816 check --select/--ignore`."""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.config import load_a816_toml
from a816.exceptions import A816ConfigError
from a816.fluff.cli import fluff_main
from a816.fluff.core import Diagnostic
from a816.fluff.selection import RuleSelection, parse_codes, unknown_codes
from a816.lsp.document import A816Document

_LONG = '"""Module."""\n; ' + "x" * 130 + "\n"  # E501


def _diag(code: str) -> Diagnostic:
    return Diagnostic(path=Path("x.s"), line=1, column=1, code=code, message="m")


def _project(tmp_path: Path, lint: str = "") -> Path:
    (tmp_path / "a816.toml").write_text(f'entrypoint = "main.s"\n{lint}', encoding="utf-8")
    source = tmp_path / "main.s"
    source.write_text(_LONG, encoding="utf-8")
    return source


def test_everything_runs_by_default() -> None:
    assert RuleSelection().allows("N802")


def test_select_keeps_its_prefixes() -> None:
    assert RuleSelection(select=("N",)).allows("N802")


def test_select_drops_the_rest() -> None:
    assert not RuleSelection(select=("N",)).allows("E501")


def test_ignore_drops_a_rule() -> None:
    assert not RuleSelection(ignore=("N802",)).allows("N802")


def test_a_parse_error_always_shows() -> None:
    assert RuleSelection(select=("N",), ignore=("E",)).allows("E0101")


def test_filter_keeps_allowed_diagnostics() -> None:
    kept = RuleSelection(ignore=("N8",)).filter([_diag("N802"), _diag("E501")])
    assert [d.code for d in kept] == ["E501"]


def test_flags_split_on_commas() -> None:
    assert parse_codes(["n802, e501", "DOC"]) == ("N802", "E501", "DOC")


def test_no_flag_is_none() -> None:
    assert parse_codes(None) is None


def test_unknown_codes_are_reported() -> None:
    assert unknown_codes(["N", "N802", "Z9"]) == ["Z9"]


def test_the_toml_lists_load(tmp_path: Path) -> None:
    _project(tmp_path, '[lint]\nselect = ["doc", "N"]\nignore = ["N802"]\n')
    config = load_a816_toml(tmp_path / "a816.toml")
    assert config is not None
    assert (config.lint_select, config.lint_ignore) == (("DOC", "N"), ("N802",))


@pytest.mark.parametrize(
    "lint",
    ['lint = "N802"\n', '[lint]\nignore = "N802"\n', "[lint]\nignore = [1]\n", '[lint]\nskip = ["N802"]\n'],
)
def test_a_malformed_lint_table_is_refused(tmp_path: Path, lint: str) -> None:
    _project(tmp_path, lint)
    with pytest.raises(A816ConfigError, match=r"\[lint\]"):
        load_a816_toml(tmp_path / "a816.toml")


def test_flags_replace_the_toml_lists(tmp_path: Path) -> None:
    _project(tmp_path, '[lint]\nselect = ["DOC"]\nignore = ["N802"]\n')
    selection = RuleSelection.of(load_a816_toml(tmp_path / "a816.toml"), ("E",), ())
    assert selection == RuleSelection(select=("E",), ignore=())


def test_an_unknown_toml_code_is_refused(tmp_path: Path) -> None:
    _project(tmp_path, '[lint]\nignore = ["N999"]\n')
    with pytest.raises(A816ConfigError, match="E0516|names no rule: N999"):
        RuleSelection.of(load_a816_toml(tmp_path / "a816.toml"))


def test_check_reports_a_hit(tmp_path: Path) -> None:
    assert fluff_main(["check", str(_project(tmp_path))]) == 1


def test_check_honours_the_toml_ignore(tmp_path: Path) -> None:
    assert fluff_main(["check", str(_project(tmp_path, '[lint]\nignore = ["E5"]\n'))]) == 0


def test_check_honours_the_toml_select(tmp_path: Path) -> None:
    assert fluff_main(["check", str(_project(tmp_path, '[lint]\nselect = ["N"]\n'))]) == 0


def test_the_ignore_flag_wins_over_the_toml(tmp_path: Path) -> None:
    source = _project(tmp_path, '[lint]\nselect = ["E501"]\n')
    assert fluff_main(["check", "--ignore", "E501", str(source)]) == 0


def test_the_select_flag_wins_over_the_toml(tmp_path: Path) -> None:
    source = _project(tmp_path, '[lint]\nignore = ["E501"]\n')
    assert fluff_main(["check", "--select", "E5", "--ignore", "", str(source)]) == 1


def test_an_unknown_flag_code_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _project(tmp_path)
    assert (fluff_main(["check", "--select", "Z9", str(source)]), "E0516" in capsys.readouterr().err) == (2, True)


def test_fix_leaves_ignored_rules_out(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = _project(tmp_path, '[lint]\nignore = ["E501"]\n')
    fluff_main(["fix", "--check", str(source)])
    assert "E501" not in capsys.readouterr().out


def test_the_lsp_honours_the_toml(tmp_path: Path) -> None:
    source = _project(tmp_path, '[lint]\nignore = ["E501"]\n')
    document = A816Document(source.as_uri(), _LONG)
    assert all(d.code != "E501" for d in document.diagnostics)


def test_the_lsp_shows_hits_without_a_lint_table(tmp_path: Path) -> None:
    source = _project(tmp_path)
    document = A816Document(source.as_uri(), _LONG)
    assert any(d.code == "E501" for d in document.diagnostics)


def test_the_lsp_skips_lint_on_a_bad_lint_table(tmp_path: Path) -> None:
    source = _project(tmp_path, '[lint]\nignore = ["N999"]\n')
    document = A816Document(source.as_uri(), _LONG)
    assert all(d.code != "E501" for d in document.diagnostics)
