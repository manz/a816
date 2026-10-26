"""A build driven through the API with no logging configured still shows
its warnings: the build's own warning recorder must not silence Python's
last-resort stderr output."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest

from a816.build_cache import _recording, recording_warnings
from a816.module_builder import build_with_imports

FAILS = ".alloc code at 0x008000 {\n    lda.l nowhere\n}\n"
WARNS = ".alloc code at 0x008000 {\n    sep #0x20\n    .a8\n    lda.w #0x1234\n}\n"


def _unconfigured(monkeypatch: pytest.MonkeyPatch, *handlers: logging.Handler) -> io.StringIO:
    """Only `handlers` on the root logger (none on `a816`, which another
    test module configures); the last resort writes to the returned
    buffer. Called in the test body: pytest attaches its own capture
    handlers after fixtures run."""
    stream = io.StringIO()
    resort = logging.StreamHandler(stream)
    resort.setLevel(logging.WARNING)
    monkeypatch.setattr(logging.getLogger("a816"), "handlers", [])
    monkeypatch.setattr(logging.getLogger(), "handlers", list(handlers))
    monkeypatch.setattr(logging, "lastResort", resort)
    return stream


def _build(tmp_path: Path, source: str = WARNS) -> int:
    (tmp_path / "main.s").write_text(source, encoding="utf-8")
    result = build_with_imports(
        tmp_path / "main.s", tmp_path / "out.ips", module_paths=[tmp_path], output_dir=tmp_path / "obj"
    )
    return result.exit_code


def test_an_unconfigured_api_build_shows_its_warnings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    last_resort = _unconfigured(monkeypatch)
    _build(tmp_path)

    assert "immediate width mismatch" in last_resort.getvalue()


def test_a_configured_handler_keeps_the_last_resort_quiet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configured = io.StringIO()
    last_resort = _unconfigured(monkeypatch, logging.StreamHandler(configured))
    _build(tmp_path)

    assert last_resort.getvalue() == ""


def test_a_configured_handler_shows_the_warning_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    configured = io.StringIO()
    _unconfigured(monkeypatch, logging.StreamHandler(configured))
    _build(tmp_path)

    assert configured.getvalue().count("immediate width mismatch") == 1


def test_an_unconfigured_failing_build_shows_its_error_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    last_resort = _unconfigured(monkeypatch)
    _build(tmp_path, FAILS)

    assert last_resort.getvalue().count("`nowhere` is not defined") == 1


def test_an_info_recorder_keeps_the_last_resort_quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    last_resort = _unconfigured(monkeypatch)
    with _recording(logging.INFO):
        logging.getLogger("a816").info("progress")

    assert last_resort.getvalue() == ""


def test_two_recorders_at_one_level_print_once(monkeypatch: pytest.MonkeyPatch) -> None:
    last_resort = _unconfigured(monkeypatch)
    with recording_warnings(), recording_warnings():
        logging.getLogger("a816").warning("twice recorded")

    assert last_resort.getvalue().count("twice recorded") == 1
