"""`build_with_imports` returns the module's own errors, not a placeholder.

Bahamut Lagoon (1.1.2): a placement-free source failed in object mode with
only "Failed to compile module '__main__'" in `result.diagnostics`; the
E0310 that said why went to the log alone.
"""

from __future__ import annotations

from pathlib import Path

from a816.module_builder import build_with_imports


def _diagnostics(tmp_path: Path, source: str) -> list[str]:
    (tmp_path / "main.s").write_text(source, encoding="utf-8")
    result = build_with_imports(
        main_source=tmp_path / "main.s",
        output_file=tmp_path / "out.sfc",
        output_format="sfc",
        output_dir=tmp_path / "obj",
    )
    assert result.exit_code == 1
    return result.diagnostics


def test_the_module_error_is_the_diagnostic(tmp_path: Path) -> None:
    (diagnostic,) = _diagnostics(tmp_path, "start:\n    .dw start\n")

    assert diagnostic.startswith("error[E0310]: code emitted outside any placement")


def test_the_diagnostic_points_at_the_source(tmp_path: Path) -> None:
    (diagnostic,) = _diagnostics(tmp_path, "start:\n    .dw start\n")

    assert "main.s:2:6" in diagnostic
