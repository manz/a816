"""`check` reports a file that doesn't parse instead of passing it.

Rules that need the AST skip a failed parse, and nothing reported the
parse error itself, so `a816 check` exited 0 on `lda #` or on a missing
`.include` (dq6) while `format --check` failed on the same file.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from a816.fluff import fluff_main

SOURCES = {
    "syntax-error": ('"""M."""\nlda #\n', "E0115"),
    "missing-include": ('"""M."""\n.include "gone.i"\n', "E0500"),
}


@pytest.mark.parametrize("source", [case[0] for case in SOURCES.values()], ids=list(SOURCES))
def test_a_file_that_does_not_parse_fails_check(tmp_path: Path, source: str) -> None:
    (tmp_path / "main.s").write_text(source, encoding="utf-8")

    assert fluff_main(["check", str(tmp_path / "main.s")]) == 1


@pytest.mark.parametrize(("source", "code"), list(SOURCES.values()), ids=list(SOURCES))
def test_check_prints_the_parse_error_at_its_line(
    tmp_path: Path, source: str, code: str, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "main.s").write_text(source, encoding="utf-8")

    fluff_main(["check", str(tmp_path / "main.s")])

    assert re.search(rf"main\.s:2:\d+ {code} ", capsys.readouterr().out)
