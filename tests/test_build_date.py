"""`BUILD_DATE` honours `SOURCE_DATE_EPOCH`, so builds that embed it can be reproducible.

It used to be the wall clock at parse: ff4 prints it on its title screen,
and two consecutive builds differed by the seconds.
"""

from __future__ import annotations

import re

import pytest

from a816.build_cache import BuildSettings, _sha
from a816.exceptions import A816Error
from a816.parse.mzparser import build_date
from a816.program import Program
from tests import StubWriter


def _settings() -> BuildSettings:
    return BuildSettings({}, [], [], [], [])


def test_the_epoch_sets_the_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1791391550")

    assert build_date() == "2026-10-07 16:45:50"


def test_without_an_epoch_the_date_is_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", build_date())


def test_a_malformed_epoch_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "yesterday")

    with pytest.raises(A816Error, match="SOURCE_DATE_EPOCH='yesterday'"):
        build_date()


def test_a_build_defines_the_epoch_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
    program = Program()

    program.assemble_string_with_emitter("*=0x008000\n    rts\n", "date.s", StubWriter())

    assert program.resolver.scopes[0].symbols["BUILD_DATE"] == "1970-01-01 00:00:00"


def test_a_new_epoch_changes_the_cache_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
    first = _settings().digest()
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1")

    assert _settings().digest() != first


def test_no_epoch_keeps_existing_cache_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Caches built before the setting existed must not all rebuild."""
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    before: dict[str, list[str]] = {
        "symbols": [],
        "experimental": [],
        "bus_map": [],
        "include_paths": [],
        "module_paths": [],
    }

    assert _settings().digest() == _sha(before)
