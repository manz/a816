"""End-to-end placement checks on the default object + link build path.

Drives `build_with_imports` and the `a816 build` CLI (the path users
actually run) over small multi-module projects and asserts that
overlapping writes honour `--overlap-mode`.
"""

from __future__ import annotations

import logging
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from a816.module_builder import BuildResult, build_with_imports


def _write_project(root: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        (root / name).write_text(body, encoding="utf-8")
    return root / "main.s"


def _build(main: Path, output_format: str = "ips", overlap_mode: str | None = None) -> BuildResult:
    return build_with_imports(
        main_source=main,
        output_file=main.parent / f"out.{output_format}",
        output_format=output_format,
        output_dir=main.parent / "obj",
        overlap_mode=overlap_mode,
    )


def _run_cli(args: list[str]) -> tuple[int, str]:
    from a816.cli import cli_main

    stderr = StringIO()
    with patch.object(sys, "argv", ["a816", *args]), patch.object(sys, "stderr", stderr):
        try:
            cli_main()
        except SystemExit as e:
            return int(e.code or 0), stderr.getvalue()
    return 0, stderr.getvalue()


_OVERLAPPING_MODULES = {
    "main.s": '.import "a"\n.import "b"\n',
    "a.s": "*= 0x008000\n    nop\n    nop\n",
    "b.s": "*= 0x008001\n    rts\n",
}


@pytest.mark.parametrize("output_format", ["ips", "sfc"])
def test_overlapping_modules_fail_the_build_by_default(tmp_path: Path, output_format: str) -> None:
    result = _build(_write_project(tmp_path, _OVERLAPPING_MODULES), output_format)
    assert result.exit_code != 0


def test_overlap_error_names_the_shared_bytes(tmp_path: Path) -> None:
    result = _build(_write_project(tmp_path, _OVERLAPPING_MODULES), overlap_mode="error")
    assert any("$000001..$000001" in d for d in result.diagnostics)


def test_overlap_error_leaves_no_partial_output(tmp_path: Path) -> None:
    main = _write_project(tmp_path, _OVERLAPPING_MODULES)
    _build(main, overlap_mode="error")
    assert not (tmp_path / "out.ips").exists()


def test_overlap_warn_mode_logs_and_builds(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    main = _write_project(tmp_path, _OVERLAPPING_MODULES)
    with caplog.at_level(logging.WARNING, logger="a816.writers"):
        result = _build(main, overlap_mode="warn")
    assert result.exit_code == 0 and any("overlaps" in r.message for r in caplog.records)


def test_overlap_off_mode_builds_silently(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    main = _write_project(tmp_path, _OVERLAPPING_MODULES)
    with caplog.at_level(logging.WARNING, logger="a816.writers"):
        result = _build(main, overlap_mode="off")
    assert result.exit_code == 0 and not caplog.records


def test_adjacent_modules_do_not_trip_the_auditor(tmp_path: Path) -> None:
    files = {**_OVERLAPPING_MODULES, "b.s": "*= 0x008002\n    rts\n"}
    assert _build(_write_project(tmp_path, files), overlap_mode="error").exit_code == 0


def test_cli_build_rejects_overlap_in_single_file(tmp_path: Path) -> None:
    main = _write_project(tmp_path, {"main.s": "*= 0x008000\n    nop\n    nop\n*= 0x008001\n    rts\n"})
    rc, _ = _run_cli(["build", str(main), "-o", str(tmp_path / "out.ips"), "--obj-dir", str(tmp_path / "obj")])
    assert rc != 0


def test_cli_build_overlap_mode_warn_builds(tmp_path: Path) -> None:
    main = _write_project(tmp_path, {"main.s": "*= 0x008000\n    nop\n    nop\n*= 0x008001\n    rts\n"})
    out = tmp_path / "out.ips"
    rc, _ = _run_cli(["build", str(main), "-o", str(out), "--obj-dir", str(tmp_path / "obj"), "--overlap-mode", "warn"])
    assert rc == 0 and out.exists()


def test_cli_explicit_link_rejects_overlap(tmp_path: Path) -> None:
    _write_project(tmp_path, _OVERLAPPING_MODULES)
    rc, stderr = _run_cli(["build", str(tmp_path / "a.s"), str(tmp_path / "b.s"), "-o", str(tmp_path / "out.ips")])
    assert rc != 0 and "overlaps" in stderr
