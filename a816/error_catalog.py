"""What each error code means, for `a816 explain E0317`.

The docs tree is not shipped, so the prose of docs/docs/errors.md's code
catalog is mirrored into `_error_catalog_data.py`, a plain module the
wheel and the onefile binary both carry. tests/test_error_catalog.py fails
when the two drift; refresh with
`UPDATE_ERROR_CATALOG=1 hatch run tests:tests tests/test_error_catalog.py`,
then `hatch run tests:format`.
"""

from __future__ import annotations

import json
import re

from a816._error_catalog_data import ENTRIES

_BULLET = re.compile(r"^- `([EW]\d{4})` (.*)$")
_CODE = re.compile(r"[EW]\d{4}\Z")
_CATEGORIES = {
    "E00": "scanner",
    "E01": "parser",
    "E02": "symbol resolution",
    "E03": "codegen",
    "E04": "linker / object files",
    "E05": "I/O / config",
}


def is_error_code(text: str) -> bool:
    return bool(_CODE.match(text))


def parse_catalog(markdown: str) -> dict[str, str]:
    """Code -> its catalog bullet, unwrapped onto one line, code dropped."""
    entries: dict[str, str] = {}
    current: str | None = None
    in_catalog = False
    for line in markdown.splitlines():
        if line.startswith("## "):
            in_catalog = line == "## Code catalog"
        match = _BULLET.match(line) if in_catalog else None
        if match:
            current = match.group(1)
            entries[current] = match.group(2)
        elif current and line.startswith("  "):
            entries[current] += " " + line.strip()
        else:
            current = None
    return entries


def render_data_module(entries: dict[str, str]) -> str:
    lines = ['"""Generated from docs/docs/errors.md by a816.error_catalog: do not edit."""', ""]
    lines.append("ENTRIES: dict[str, str] = {")
    # A JSON string is a Python literal, double-quoted as ruff formats it.
    lines += [f"    {json.dumps(code)}: {json.dumps(text, ensure_ascii=False)}," for code, text in entries.items()]
    lines.append("}")
    return "\n".join(lines) + "\n"


def explanation(code: str) -> str | None:
    """The catalog entry for `code`, or None for a code it doesn't hold."""
    return ENTRIES.get(code)


def category(code: str) -> str:
    """The code range `code` falls in, as errors.md's Categories list them."""
    if code.startswith("W"):
        return "warning"
    return _CATEGORIES.get(code[:3], "no category")
