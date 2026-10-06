"""The shipped stdlib is `a816 format`-clean, like the projects that import it."""

from pathlib import Path

import pytest

import a816
from a816.formatter import A816Formatter

_STDLIB = sorted((Path(a816.__file__).parent / "stdlib").rglob("*.s"))


@pytest.mark.parametrize("path", _STDLIB, ids=lambda path: path.name)
def test_stdlib_file_is_formatted(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert A816Formatter().format_text(text) == text
