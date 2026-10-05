"""An undefined symbol passed to a macro is reported at the call.

The error used to name the macro parameter (`pointer`) inside the macro
body, so a macro used from many places gave no clue which caller was wrong.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tests import BANK_40_MAP, build_rom

_MACRO = ".macro load_ptr(pointer) {\n    ldy.w #pointer - 0x8000\n}\n"


def _log(tmp_path: Path, caplog: pytest.LogCaptureFixture, call: str) -> str:
    source = BANK_40_MAP + _MACRO + ".alloc at 0x400000 {\n    " + call + "\n}\n"
    with caplog.at_level(logging.ERROR):
        rc, _rom = build_rom(tmp_path, {"main.s": source})
    assert rc != 0
    return caplog.text


def test_the_error_names_the_argument_at_the_call(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    log = _log(tmp_path, caplog, "load_ptr(missing_label)")
    assert "`missing_label` is not defined in the current scope" in log
    assert "main.s:6:14" in log


def test_the_hint_names_the_macro_parameter_and_its_use(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    log = _log(tmp_path, caplog, "load_ptr(missing_label + 2)")
    assert "passed to `load_ptr` as `pointer`, used at" in log
    assert "main.s:3" in log


def test_a_forward_reference_argument_still_resolves(tmp_path: Path) -> None:
    source = BANK_40_MAP + _MACRO + ".alloc at 0x400000 {\n    load_ptr(later)\nlater:\n}\n"
    rc, rom = build_rom(tmp_path, {"main.s": source})
    assert (rc, rom[:3].hex(" ")) == (0, "a0 03 80")
