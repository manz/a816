"""Coverage for the docstring-coverage lints (DOC001, DOC002)."""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.fluff import fluff_main
from a816.fluff_lint import lint_file


def test_doc001_flags_missing_module_docstring(tmp_path: Path) -> None:
    src = tmp_path / "main.s"
    src.write_text(
        """; just a comment header
"""
        '"""Documented."""\n'
        "main:\n"
        "    rts\n",
        encoding="utf-8",
    )
    diags = lint_file(src)
    codes = [d.code for d in diags]
    assert "DOC001" not in codes  # leading docstring is present (after the comment)


def test_doc001_flags_when_no_leading_docstring(tmp_path: Path) -> None:
    src = tmp_path / "main.s"
    src.write_text("main:\n    rts\n", encoding="utf-8")
    diags = lint_file(src)
    codes = [d.code for d in diags]
    assert "DOC001" in codes


def test_doc002_flags_undocumented_macro(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\n.macro public_macro() {\n    rts\n}\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    codes = [d.code for d in diags]
    assert "DOC002" in codes
    assert any("public_macro" in d.message for d in diags)


def test_doc002_skips_underscore_prefixed_names(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\n.macro _private_macro() {\n    rts\n}\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    assert all(d.code != "DOC002" for d in diags)


def test_doc002_accepts_attached_docstring(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\n"""Documented."""\n.macro documented_macro() {\n    rts\n}\n',
        encoding="utf-8",
    )
    diags = lint_file(src)
    assert all(d.code != "DOC002" for d in diags)


def test_check_command_reports_and_exits_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "main.s"
    src.write_text("main:\n    rts\n", encoding="utf-8")
    rc = fluff_main(["check", str(src)])
    captured = capsys.readouterr()
    assert rc == 1
    assert "DOC001" in captured.out


def test_check_command_clean_exits_zero(tmp_path: Path) -> None:
    src = tmp_path / "lib.s"
    src.write_text(
        '"""Module."""\n"""Documented."""\n.macro foo() {\n    rts\n}\n',
        encoding="utf-8",
    )
    assert fluff_main(["check", str(src)]) == 0
