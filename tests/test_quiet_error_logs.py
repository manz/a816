"""Caught-error paths log a clean message at ERROR and stash the
Python traceback at DEBUG. Default CLI log level shows the message
only; `--verbose` flips to DEBUG and the traceback comes back."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pytest

from a816.module_builder import build_with_imports, build_with_imports_direct
from a816.program import Program
from a816.writers import ObjectWriter


def _write(src: str, name: str = "main.s") -> tuple[Path, Path]:
    tmp = Path(tempfile.mkdtemp())
    path = tmp / name
    path.write_text(src, encoding="utf-8")
    return tmp, path


def test_object_emit_nodeerror_logs_error_not_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # `nop #0x00` raises NodeError during emit (nop has no immediate
    # addressing). We want: ERROR with the message, DEBUG with the
    # stashed traceback, no exception bubbling.
    tmp, asm = _write("nop #0x00\n")
    obj = tmp / "out.o"
    writer = ObjectWriter(str(obj))
    writer.begin()
    with caplog.at_level(logging.DEBUG):
        rc = Program().assemble_with_object_emitter(str(asm), writer)
    assert rc == -1
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    debug_traces = [r for r in caplog.records if r.levelno == logging.DEBUG and "traceback" in r.message.lower()]
    assert error_records, "expected an ERROR-level diagnostic"
    assert debug_traces, "expected the traceback stashed at DEBUG"
    # The DEBUG record carries exc_info (the actual traceback).
    assert debug_traces[0].exc_info is not None


def test_module_builder_failure_demotes_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Module with a codegen error -> build_with_imports catches the
    # RuntimeError, logs the user-facing message at ERROR, traceback
    # at DEBUG.
    tmp, main = _write("nop #0x00\n")
    with caplog.at_level(logging.DEBUG):
        result = build_with_imports(main, tmp / "out.ips", output_dir=tmp / "build")
    assert result.exit_code != 0
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    debug_traces = [r for r in caplog.records if r.levelno == logging.DEBUG and "traceback" in r.message.lower()]
    assert error_records
    assert debug_traces
    assert debug_traces[0].exc_info is not None


def test_module_builder_failure_quiet_at_info_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # At INFO level (CLI default), the DEBUG traceback record does
    # not appear. The user only sees the rendered error.
    tmp, main = _write("nop #0x00\n")
    with caplog.at_level(logging.INFO):
        build_with_imports(main, tmp / "out.ips", output_dir=tmp / "build")
    traceback_records = [r for r in caplog.records if "traceback" in r.message.lower()]
    assert traceback_records == [], "no traceback should appear at INFO level"


def test_module_builder_direct_failure_demotes_traceback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Direct-mode (position-dependent) build path has its own
    # top-level except. Same demotion behavior expected.
    tmp, main = _write("*=0x008000\nnop #0x00\n")
    with caplog.at_level(logging.DEBUG):
        result = build_with_imports_direct(main, tmp / "out.ips", output_dir=tmp / "build")
    assert result.exit_code != 0
    debug_traces = [r for r in caplog.records if r.levelno == logging.DEBUG and "traceback" in r.message.lower()]
    assert debug_traces
    assert debug_traces[0].exc_info is not None


def test_discover_imports_oserror_demotes_traceback(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `_discover_imports_recursive` opens source files to scan for
    # nested .imports. Patch Path.read_text to raise PermissionError
    # mid-walk to exercise the OSError handler.
    from a816 import module_builder

    tmp, main = _write("*=0x008000\nnop\n")

    original_read = Path.read_text

    def fake_read(self: Path, *a: object, **kw: object) -> str:
        if self == main:
            raise PermissionError(f"denied: {self}")
        return original_read(self, *a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", fake_read)
    builder = module_builder.ModuleBuilder(output_dir=tmp / "build")
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(OSError):
            builder._discover_imports_recursive(main, "__main__")
    debug_traces = [r for r in caplog.records if r.levelno == logging.DEBUG and "traceback" in r.message.lower()]
    assert debug_traces
    assert debug_traces[0].exc_info is not None


def test_ips_apply_value_error_demotes_traceback(
    caplog: pytest.LogCaptureFixture,
    tmp_path: Path,
) -> None:
    from a816.xdds import _apply_ips_to_temp

    rom = tmp_path / "rom.sfc"
    rom.write_bytes(b"\x00" * 0x10000)
    bad_ips = tmp_path / "bad.ips"
    bad_ips.write_bytes(b"not-an-ips-file")
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(SystemExit):
            _apply_ips_to_temp(rom, bad_ips)
    error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
    debug_traces = [r for r in caplog.records if r.levelno == logging.DEBUG and "traceback" in r.message.lower()]
    assert error_records
    assert debug_traces
    assert debug_traces[0].exc_info is not None


def test_verbose_flag_enables_debug_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    # `--verbose` in argv flips logging.basicConfig to DEBUG so the
    # stashed tracebacks become visible. We don't fully run cli_main
    # (it would call sys.exit); just verify the level-selection logic.
    import sys

    from a816 import cli

    captured: dict[str, int] = {}

    def fake_basic_config(**kwargs: object) -> None:
        level = kwargs["level"]
        assert isinstance(level, int)
        captured["level"] = level

    monkeypatch.setattr(logging, "basicConfig", fake_basic_config)
    monkeypatch.setattr(sys, "argv", ["a816", "--verbose", "--help"])
    monkeypatch.setattr(cli, "_dispatch_subcommand", lambda _argv: 0)
    with pytest.raises(SystemExit):
        cli.cli_main()
    assert captured["level"] == logging.DEBUG

    captured.clear()
    monkeypatch.setattr(sys, "argv", ["a816", "--help"])
    with pytest.raises(SystemExit):
        cli.cli_main()
    assert captured["level"] == logging.INFO
