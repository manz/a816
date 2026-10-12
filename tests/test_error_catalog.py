"""`a816 explain` answers for every error code, from errors.md's catalog.

ff4 (rc1) ran `a816 explain E0317` while chasing an E0317 and got "unknown
rule": explain only knew lint rules.

Refresh the shipped copy with
`UPDATE_ERROR_CATALOG=1 hatch run tests:tests tests/test_error_catalog.py`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from a816 import error_codes
from a816._error_catalog_data import ENTRIES
from a816.error_catalog import category, explanation, parse_catalog, render_data_module
from a816.error_codes import ErrorCode
from a816.fluff import fluff_main

_DOCS = Path(__file__).parent.parent / "docs" / "docs" / "errors.md"
_DATA = Path(__file__).parent.parent / "a816" / "_error_catalog_data.py"
_REGISTERED = sorted(value.code for value in vars(error_codes).values() if isinstance(value, ErrorCode))


def test_the_shipped_catalog_matches_the_docs() -> None:
    """Compared as data: ruff may requote the generated module."""
    catalog = parse_catalog(_DOCS.read_text(encoding="utf-8"))
    if os.environ.get("UPDATE_ERROR_CATALOG"):
        _DATA.write_text(render_data_module(catalog), encoding="utf-8")
        pytest.skip("catalog refreshed")

    assert ENTRIES == catalog, "refresh it: UPDATE_ERROR_CATALOG=1, then hatch run tests:format"


@pytest.mark.parametrize("code", _REGISTERED)
def test_every_registered_code_has_an_explanation(code: str) -> None:
    assert explanation(code)


def test_a_wrapped_bullet_is_unwrapped() -> None:
    markdown = "## Code catalog\n\n- `E0001` first line\n  second line.\n- `E0002` next.\n"

    assert parse_catalog(markdown) == {"E0001": "first line second line.", "E0002": "next."}


def test_bullets_outside_the_catalog_are_ignored() -> None:
    markdown = "## Anatomy\n\n- `E0200` an example.\n\n## Code catalog\n\n- `E0001` real.\n\n## LSP\n\n- `E0002` no.\n"

    assert parse_catalog(markdown) == {"E0001": "real."}


@pytest.mark.parametrize(
    ("code", "expected"),
    [("E0317", "codegen"), ("E0510", "I/O / config"), ("W0009", "warning"), ("E0900", "no category")],
)
def test_category_follows_the_code_range(code: str, expected: str) -> None:
    assert category(code) == expected


def test_explain_prints_an_error_code(capsys: pytest.CaptureFixture[str]) -> None:
    code = fluff_main(["explain", "e0317"])

    out = capsys.readouterr().out
    assert (code, out.startswith("E0317  "), "codegen" in out) == (0, True, True)


def test_explain_of_an_unknown_code_names_its_range(capsys: pytest.CaptureFixture[str]) -> None:
    code = fluff_main(["explain", "E0399"])

    assert (code, "E0399" in capsys.readouterr().err) == (2, True)


def test_a_lint_rule_still_explains_as_a_rule(capsys: pytest.CaptureFixture[str]) -> None:
    fluff_main(["explain", "W0002"])

    assert "Bad:" in capsys.readouterr().out
