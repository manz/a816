"""A module's private (`_`) declarations are not visible to its importers.

`.import` inlines a module's compile-time declarations so the importer can
use its macros, structs and constants. Private ones came along too, so an
importer could read another module's `_SIZE`, call its `_macro()` or cast to
its `.struct _T`. They are still inlined (the owner's public macros and
constants are built on them), but a reference written outside the owning
module is now an error that says so.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from a816.symbols import Resolver, _canonical_file
from tests import BANK_40_MAP, build_rom

_LIB = """_SIZE = 4
.macro _emit_nop() {
    nop
}
.struct _T {
    byte a
    byte b
}
.macro load_size() {
    lda #_SIZE
    _emit_nop()
}
PUBLIC_SIZE = _SIZE + 1
"""


def _build(tmp_path: Path, body: str, caplog: pytest.LogCaptureFixture, lib: str = _LIB) -> tuple[int, bytes, str]:
    main = BANK_40_MAP + '.import "lib"\n.alloc c at 0x400000 {\n' + body + "}\n"
    with caplog.at_level(logging.ERROR):
        rc, rom = build_rom(tmp_path, {"main.s": main, "lib.s": lib})
    return rc, rom, caplog.text


@pytest.mark.parametrize(
    ("body", "name"),
    [
        ("    lda #_SIZE\n", "_SIZE"),
        ("    _emit_nop()\n", "_emit_nop"),
        ("    lda #_T.b\n", "_T.b"),
    ],
)
def test_an_importer_cannot_name_another_module_s_private_declaration(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, body: str, name: str
) -> None:
    rc, _rom, log = _build(tmp_path, body, caplog)
    assert rc != 0
    assert f"`{name}` is private to module `lib`; drop the leading `_` there to export it" in log


def test_the_owner_s_public_macro_still_uses_its_private_names(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rc, rom, _log = _build(tmp_path, "    load_size()\n", caplog)
    assert (rc, rom[:3].hex(" ")) == (0, "a9 04 ea")


def test_the_owner_s_public_constant_still_uses_its_private_names(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rc, rom, _log = _build(tmp_path, "    lda #PUBLIC_SIZE\n", caplog)
    assert (rc, rom[:2].hex(" ")) == (0, "a9 05")


def test_private_names_from_the_owner_s_include_count_as_the_owner_s(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    (tmp_path / "lib_defs.i").write_text("_STRIDE = 3\n")
    lib = '.include "lib_defs.i"\n.macro load_stride() {\n    lda #_STRIDE\n}\n'
    rc, rom, _log = _build(tmp_path, "    load_stride()\n", caplog, lib=lib)
    assert (rc, rom[:2].hex(" ")) == (0, "a9 03")


def test_a_module_s_own_private_names_are_unaffected(tmp_path: Path) -> None:
    main = BANK_40_MAP + "_OWN = 7\n.alloc c at 0x400000 {\n    lda #_OWN\n}\n"
    rc, rom = build_rom(tmp_path, {"main.s": main})
    assert (rc, rom[:2].hex(" ")) == (0, "a9 07")


def test_an_lsp_file_uri_names_the_same_file(tmp_path: Path) -> None:
    path = tmp_path / "lib.s"
    path.write_text("")
    assert _canonical_file(path.as_uri()) == _canonical_file(str(path))


def test_a_reference_without_a_position_is_not_judged() -> None:
    resolver = Resolver()
    resolver.private_owners["_X"] = ("lib", frozenset({"/lib.s"}))
    assert resolver.foreign_private_owner("_X", None) is None


def test_a_name_no_import_owns_is_not_judged() -> None:
    resolver = Resolver()
    resolver.private_owners["_X"] = ("lib", frozenset({"/lib.s"}))
    assert resolver.foreign_private_owner("_Y", None) is None
