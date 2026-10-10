"""W0002: an `.if` on a name the project defines nowhere reads as false.

ff4 (rc3) removed the always-1 flag `BATTLE_MONSTERS_VWF` from config.i; an
`.if` on it in ff4.s kept building with its body silently dropped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.fluff import lint_text


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "a816.toml").write_text('entrypoint = "main.s"\n', encoding="utf-8")
    (tmp_path / "config.i").write_text("FLAG = 1\n", encoding="utf-8")
    return tmp_path


def _hits(project: Path, source: str) -> list[str]:
    path = project / "main.s"
    path.write_text(source, encoding="utf-8")
    return [d.message.split("`")[3] for d in lint_text(source, path) if d.code == "W0002"]


def test_a_name_defined_nowhere_warns(project: Path) -> None:
    assert _hits(project, '"""M."""\n.if REMOVED_FLAG {\n    .db 1\n}\n') == ["REMOVED_FLAG"]


def test_a_name_defined_in_another_project_file_is_quiet(project: Path) -> None:
    assert _hits(project, '"""M."""\n.include "config.i"\n.if FLAG {\n    .db 1\n}\n') == []


def test_a_name_defined_in_a_file_not_included_here_is_quiet(project: Path) -> None:
    """Project-wide, not per translation unit: a module's flag may come from an import."""
    assert _hits(project, '"""M."""\n.if FLAG {\n    .db 1\n}\n') == []


def test_a_nested_if_is_checked(project: Path) -> None:
    assert _hits(project, '"""M."""\n.if FLAG {\n    .if GONE {\n        .db 1\n    }\n}\n') == ["GONE"]


def test_a_dotted_name_counts_by_its_base(project: Path) -> None:
    source = '"""M."""\n.pool p { range 0x008000 0x00ffff }\n.if p.capacity > 4 {\n    .db 1\n}\n'

    assert _hits(project, source) == []


def test_a_macro_parameter_is_defined(project: Path) -> None:
    source = '"""M."""\n.macro m(debug) {\n    .if debug {\n        .db 1\n    }\n}\n'

    assert _hits(project, source) == []


def test_noqa_silences_a_dash_d_name(project: Path) -> None:
    assert _hits(project, '"""M."""\n.if DEBUG { ; noqa: W0002\n    .db 1\n}\n') == []
