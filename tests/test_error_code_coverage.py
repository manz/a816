"""Every assembler diagnostic carries a stable code, and every code is documented.

Uncoded NodeErrors used to accumulate ("some older codegen diagnostics
still render without a code"); these keep the catalog closed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from a816 import error_codes
from a816.error_codes import ErrorCode
from a816.module_builder import build_with_imports

PACKAGE = Path(error_codes.__file__).parent
CATALOG = PACKAGE.parent / "docs" / "docs" / "errors.md"


def _uncoded_node_errors() -> list[str]:
    found = []
    for path in sorted(PACKAGE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "NodeError"
                and not any(keyword.arg == "code" for keyword in node.keywords)
            ):
                found.append(f"{path.relative_to(PACKAGE.parent)}:{node.lineno}")
    return found


def test_every_node_error_has_a_code() -> None:
    assert _uncoded_node_errors() == []


def test_every_code_is_in_the_catalog() -> None:
    documented = set(re.findall(r"`(E\d{4})`", CATALOG.read_text(encoding="utf-8")))
    registered = {value.code for value in vars(error_codes).values() if isinstance(value, ErrorCode)}

    assert sorted(registered - documented) == []


def _failed_build(tmp_path: Path, source: str, caplog: pytest.LogCaptureFixture) -> str:
    main = tmp_path / "main.s"
    main.write_text(source, encoding="utf-8")
    result = build_with_imports(main, tmp_path / "out.ips", module_paths=[tmp_path], output_dir=tmp_path / "obj")
    assert result.exit_code != 0
    return caplog.text


def test_a_missing_module_reports_its_code(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert "E0212" in _failed_build(tmp_path, '.import "nowhere"\n', caplog)


def test_a_failed_module_is_reported_once(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """No trailing `Build failed: Failed to compile module` after the located error."""
    assert "Build failed" not in _failed_build(tmp_path, ".alloc at 0x008000 {\n    lda #undefined\n}\n", caplog)


def test_a_failed_module_still_reports_its_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert "E0200" in _failed_build(tmp_path, ".alloc at 0x008000 {\n    lda #undefined\n}\n", caplog)
