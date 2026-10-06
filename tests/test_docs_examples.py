"""Every `ca65` / `python` example in the docs and the README is checked.

The docs drifted from the code more than once (`.map low_rom`, `.dd`, bare
named scopes, `__size` names); this keeps each example honest. A comment on
the line before a fence sets how the example is checked:

* none: a `ca65` block parses without error (fragments, snippets that
  name symbols defined elsewhere); a `python` block runs, as a script, in
  an empty directory;
* `<!-- example: build -->`: a complete program that builds with
  `a816 build` (object mode + link);
* `<!-- example: error E0xxx -->`: it fails with that code;
* `<!-- example: skip -->`: illustrative only (pseudo-syntax, output).

Multi-file examples live as real projects under `docs/examples/<name>/`
(an `a816.toml` naming the entrypoint, the sources, optionally a golden
`expected.ips`); pages include their files with the snippets extension
(`--8<-- "<name>/main.s"`), so what renders is what CI builds. Each folder
must lint clean, be formatted, build, and match its golden output.
"""

from __future__ import annotations

import logging
import re
import runpy
from dataclasses import dataclass
from pathlib import Path

import pytest

from a816.module_builder import build_with_imports
from a816.parse.mzparser import A816Parser

ROOT = Path(__file__).resolve().parent.parent
# README.md links to docs/docs/index.md: collect each file once.
_SOURCES = sorted({path.resolve() for path in (ROOT / "docs" / "docs").rglob("*.md")})
_EXAMPLE_DIRS = sorted(path.parent for path in (ROOT / "docs" / "examples").glob("*/a816.toml"))
_FENCE = re.compile(r"^```(ca65|python)[^\n]*\n(.*?)^```", re.MULTILINE | re.DOTALL)
_MARKER = re.compile(r"<!--\s*example:\s*(build|skip|error\s+E\d{4})\s*-->\s*$")


@dataclass(frozen=True)
class Example:
    path: Path
    line: int
    language: str
    code: str
    mode: str

    @property
    def id(self) -> str:
        return f"{self.path.relative_to(ROOT)}:{self.line}"


def _examples() -> list[Example]:
    out: list[Example] = []
    for path in _SOURCES:
        text = path.read_text(encoding="utf-8")
        for match in _FENCE.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            before = text[: match.start()].rstrip("\n").rsplit("\n", 1)[-1]
            marker = _MARKER.search(before)
            language, code = match.group(1), match.group(2)
            default = "parse" if language == "ca65" else "run"
            out.append(Example(path, line, language, code, marker.group(1) if marker else default))
    return out


_EXAMPLES = _examples()


@pytest.mark.parametrize("example", _EXAMPLES, ids=[e.id for e in _EXAMPLES])
def test_docs_example(
    example: Example, tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    if example.mode == "skip":
        pytest.skip("illustrative example")
    if example.mode == "run":
        script = tmp_path / "example.py"
        script.write_text(example.code)
        monkeypatch.chdir(tmp_path)
        runpy.run_path(str(script), run_name="__main__")
        return
    if example.mode == "parse":
        parsed = A816Parser.parse_as_ast(example.code, filename=example.id)
        assert parsed.parse_error is None, parsed.parse_error
        return
    (tmp_path / "main.s").write_text(example.code)
    with caplog.at_level(logging.ERROR):
        result = build_with_imports(
            tmp_path / "main.s",
            tmp_path / "out.sfc",
            output_format="sfc",
            output_dir=tmp_path / "obj",
            use_a816_toml=False,
        )
    if example.mode == "build":
        assert result.exit_code == 0, caplog.text
        return
    expected = example.mode.split()[1]
    assert result.exit_code != 0
    assert expected in caplog.text, caplog.text


@pytest.mark.parametrize("folder", _EXAMPLE_DIRS, ids=[path.name for path in _EXAMPLE_DIRS])
def test_docs_example_project(folder: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A `docs/examples/<name>/` project lints, is formatted, builds and
    matches its golden output."""
    import shutil
    import tomllib

    from a816.fluff import fluff_main

    project = tmp_path / folder.name
    shutil.copytree(folder, project)
    sources = [str(path) for path in sorted(project.rglob("*.s"))]
    assert fluff_main(["check", *sources]) == 0
    assert fluff_main(["format", "--check", *sources]) == 0
    entry = project / tomllib.loads((project / "a816.toml").read_text())["entrypoint"]
    golden = folder / "expected.ips"
    output = tmp_path / "out.ips"
    with caplog.at_level(logging.ERROR):
        result = build_with_imports(entry, output, output_format="ips", output_dir=tmp_path / "obj")
    assert result.exit_code == 0, caplog.text
    if golden.exists():
        assert output.read_bytes() == golden.read_bytes()
