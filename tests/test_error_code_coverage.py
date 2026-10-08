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
    documented = set(re.findall(r"`([EW]\d{4})`", CATALOG.read_text(encoding="utf-8")))
    registered = {value.code for value in vars(error_codes).values() if isinstance(value, ErrorCode)}

    assert sorted(registered - documented) == []


def _failed_build(tmp_path: Path, source: str, caplog: pytest.LogCaptureFixture) -> str:
    (tmp_path / "m.bin").write_bytes(b"ABCDE")
    main = tmp_path / "main.s"
    main.write_text(source, encoding="utf-8")
    result = build_with_imports(
        main, tmp_path / "out.ips", module_paths=[tmp_path], include_paths=[tmp_path], output_dir=tmp_path / "obj"
    )
    assert result.exit_code != 0
    return caplog.text


def test_a_missing_module_reports_its_code(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert "E0212" in _failed_build(tmp_path, '.import "nowhere"\n', caplog)


def test_a_failed_module_is_reported_once(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """No trailing `Build failed: Failed to compile module` after the located error."""
    assert "Build failed" not in _failed_build(tmp_path, ".alloc at 0x008000 {\n    lda #undefined\n}\n", caplog)


def test_a_failed_module_still_reports_its_error(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    assert "E0200" in _failed_build(tmp_path, ".alloc at 0x008000 {\n    lda #undefined\n}\n", caplog)


# Names a generator needs while expanding; a miss used to escape as `Build failed: NAME`.
ESCAPING = {
    "sizeof-in-for-bound": (
        (
            ".alloc dte at 0x008000 {\n    .db 1, 2\n}\n.alloc pad at 0x008100 {\n"
            "    .for i := 0, 8 - sizeof(dte) {\n        .db 0xFF\n    }\n}\n"
        ),
        "E0321",
    ),
    "undefined-for-bound": (".alloc a at 0x008000 {\n    .for i := 0, nowhere {\n        .db 0\n    }\n}\n", "E0200"),
    "incbin-size-in-for-bound": (
        (
            '.alloc m at 0x008000 {\n    .incbin "m.bin"\n}\n.alloc pad at 0x008100 {\n'
            "    .for i := 0, 0x20 - m_bin__size {\n        .db 0\n    }\n}\n"
        ),
        "E0321",
    ),
}


@pytest.mark.parametrize("source", [case[0] for case in ESCAPING.values()], ids=list(ESCAPING))
def test_an_error_while_expanding_is_never_a_bare_build_failure(
    tmp_path: Path, source: str, caplog: pytest.LogCaptureFixture
) -> None:
    assert "Build failed" not in _failed_build(tmp_path, source, caplog)


@pytest.mark.parametrize(("source", "code"), list(ESCAPING.values()), ids=list(ESCAPING))
def test_an_error_while_expanding_carries_its_code(
    tmp_path: Path, source: str, code: str, caplog: pytest.LogCaptureFixture
) -> None:
    assert f"error[{code}]" in _failed_build(tmp_path, source, caplog)


def test_a_missing_incbin_file_is_located_and_coded(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """It printed a bare `Build failed: [Errno 2] No such file or directory`."""
    text = _failed_build(tmp_path, '.alloc m at 0x008000 {\n    .incbin "gone.bin"\n}\n', caplog)

    assert "error[E0500]" in text


def test_a_missing_include_file_is_located_and_coded(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """It printed a bare `[Errno 2] No such file or directory` (dq6)."""
    text = _failed_build(tmp_path, '.include "gone.i"\n', caplog)

    assert "error[E0500]" in text


def test_a_missing_include_lists_the_searched_paths(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    text = _failed_build(tmp_path, '.include "gone.i"\n', caplog)

    assert f"searched {tmp_path.resolve()}" in text
