"""Module constants are seeded along `.import` edges, not compile order.

A module used to see every constant exported by modules compiled before it,
imported or not, so visibility hung on module names and an edit to a
non-imported provider left its users stale. Imported (also transitively)
constants resolve; anything else is E0200, whose hint names the `.import`
to add (1.1.0a42 to a51 resolved it with a warning).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tests import BANK_40_MAP, build_rom

_USER = ".alloc c at 0x400000 {\n    lda.b #LEAK\n}\n"
_UNIMPORTED = {"main.s": BANK_40_MAP + '.import "aprov"\n.import "user"\n', "user.s": _USER, "aprov.s": "LEAK = 0x42\n"}


def _first_op(tmp_path: Path, files: dict[str, str]) -> tuple[int, bytes]:
    rc, rom = build_rom(tmp_path, files)
    return rc, rom[:2]


def test_a_transitively_imported_constant_resolves(tmp_path: Path) -> None:
    files = {
        "main.s": BANK_40_MAP + '.import "user"\n',
        "user.s": '.import "mid"\n' + _USER,
        "mid.s": '.import "aprov"\n',
        "aprov.s": "LEAK = 0x42\n",
    }
    assert _first_op(tmp_path, files) == (0, b"\xa9\x42")


def test_an_unimported_constant_fails_the_build(tmp_path: Path) -> None:
    rc, _rom = _first_op(tmp_path, _UNIMPORTED)

    assert rc != 0


def test_the_error_names_the_import_to_add(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    _first_op(tmp_path, _UNIMPORTED)

    assert '`LEAK` is a constant of module `aprov`: add `.import "aprov"`' in caplog.text


def test_an_imported_constant_does_not_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    files = {
        "main.s": BANK_40_MAP + '.import "user"\n',
        "user.s": '.import "aprov"\n' + _USER,
        "aprov.s": "LEAK = 0x42\n",
    }
    with caplog.at_level(logging.WARNING):
        assert _first_op(tmp_path, files) == (0, b"\xa9\x42")
    assert "add `.import" not in caplog.text
