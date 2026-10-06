"""Object-mode `.import` inlines only the declarations inside `.if` /
`.scope` / `.for` bodies.

The imported module's `.o` owns its bytes. Inlining those bodies whole
re-emitted the module's code into every importer's `.o` (duplicate
writes at link time) and ran its `*=`-less code in the importer's
placement context (E0310 blamed on `__main__`).
"""

from __future__ import annotations

from pathlib import Path

from a816.module_builder import BuildResult, build_with_imports
from a816.object_file import ObjectFile

_NOP_RTS = b"\xea\x60"


def _build(root: Path, mod: str, main: str) -> BuildResult:
    (root / "mod.s").write_text(mod, encoding="utf-8")
    (root / "main.s").write_text(main, encoding="utf-8")
    return build_with_imports(
        main_source=root / "main.s",
        output_file=root / "out.ips",
        module_paths=[root],
        output_dir=root / "obj",
    )


def _main_code(root: Path) -> bytes:
    obj = ObjectFile.from_file(str(root / "obj" / "__main__.o"))
    return b"".join(section.code for section in obj.sections)


_IF_ALLOC_MOD = "FLAG := 1\n.if FLAG {\n    .alloc vfun at 0x009000 {\n        nop\n        rts\n    }\n}\n"
_MAIN = '.import "mod"\n*=0x008000\nmain:\n    rts\n'


def test_if_alloc_in_imported_module_builds(tmp_path: Path) -> None:
    result = _build(tmp_path, _IF_ALLOC_MOD, _MAIN)
    assert result.exit_code == 0, result.diagnostics


def test_if_alloc_bytes_stay_out_of_the_importer_object(tmp_path: Path) -> None:
    _build(tmp_path, _IF_ALLOC_MOD, _MAIN)
    assert _NOP_RTS not in _main_code(tmp_path)


def test_if_alloc_name_still_links_from_the_importer(tmp_path: Path) -> None:
    main = '.import "mod"\n*=0x008000\nmain:\n    jsr.l vfun\n    rts\n'
    result = _build(tmp_path, _IF_ALLOC_MOD, main)
    assert result.symbol_map.get("vfun") == 0x009000, result.diagnostics


_SCOPE_MOD = "*=0x009000\n.scope vwf {\n    WIDTH := 8\nrender:\n    nop\n    rts\n}\n"


def test_scope_code_after_star_eq_does_not_trip_unplaced_in_main(tmp_path: Path) -> None:
    result = _build(tmp_path, _SCOPE_MOD, _MAIN)
    assert result.exit_code == 0, result.diagnostics


def test_scope_code_stays_out_of_the_importer_object(tmp_path: Path) -> None:
    _build(tmp_path, _SCOPE_MOD, _MAIN)
    assert _NOP_RTS not in _main_code(tmp_path)


def test_scope_constant_visible_to_importer(tmp_path: Path) -> None:
    main = '.import "mod"\n*=0x008000\nmain:\n    lda.b #vwf.WIDTH\n    rts\n'
    _build(tmp_path, _SCOPE_MOD, main)
    assert _main_code(tmp_path) == b"\xa9\x08\x60"


def test_scope_label_links_to_the_owning_module(tmp_path: Path) -> None:
    main = '.import "mod"\n*=0x008000\nmain:\n    jsr.l vwf.render\n    rts\n'
    _build(tmp_path, _SCOPE_MOD, main)
    assert (tmp_path / "out.ips").read_bytes().find(b"\x22\x00\x90\x00") != -1


_IF_MACRO_MOD = (
    "CONFIG := 1\n"
    ".if CONFIG {\n"
    "    MAGIC := 0x42\n"
    "    .macro load_magic() {\n"
    "        lda.b #MAGIC\n"
    "    }\n"
    "    .alloc conf_code at 0x009000 {\n"
    "        nop\n"
    "        rts\n"
    "    }\n"
    "} else {\n"
    "    MAGIC := 0x00\n"
    "}\n"
)


def test_macro_declared_inside_if_is_usable_by_importer(tmp_path: Path) -> None:
    main = '.import "mod"\n*=0x008000\nmain:\n    load_magic()\n    rts\n'
    _build(tmp_path, _IF_MACRO_MOD, main)
    assert _main_code(tmp_path) == b"\xa9\x42\x60"


_FOR_MOD = "*=0x009000\n.for k := 0, 3 {\n    .db k\n}\n.scope tbl {\n    COUNT := 3\n}\n"


def test_for_body_bytes_stay_out_of_the_importer_object(tmp_path: Path) -> None:
    main = '.import "mod"\n*=0x008000\nmain:\n    lda.b #tbl.COUNT\n    rts\n'
    _build(tmp_path, _FOR_MOD, main)
    assert _main_code(tmp_path) == b"\xa9\x03\x60"


_SCOPE_INCLUDE_MOD = '*=0x009000\n.scope defs {\n    .include "defs.i"\n    nop\n}\n'


def test_include_inside_scope_keeps_its_declarations(tmp_path: Path) -> None:
    (tmp_path / "defs.i").write_text("ROWS := 5\n", encoding="utf-8")
    main = '.import "mod"\n*=0x008000\nmain:\n    lda.b #defs.ROWS\n    rts\n'
    _build(tmp_path, _SCOPE_INCLUDE_MOD, main)
    assert _main_code(tmp_path) == b"\xa9\x05\x60"


def test_extern_declared_inside_if_is_kept(tmp_path: Path) -> None:
    (tmp_path / "other.s").write_text("*=0x00a000\nfar_func:\n    rtl\n", encoding="utf-8")
    mod = '.import "other"\n.if 1 {\n    .extern far_func\n}\n*=0x009000\n    jsl far_func\n'
    main = '.import "other"\n.import "mod"\n*=0x008000\nmain:\n    jsl far_func\n    rts\n'
    result = _build(tmp_path, mod, main)
    assert result.symbol_map.get("far_func") == 0x00A000, result.diagnostics


def test_if_with_only_code_is_dropped_from_the_importer(tmp_path: Path) -> None:
    mod = "*=0x009000\n.if 1 {\n    nop\n    rts\n}\n"
    _build(tmp_path, mod, _MAIN)
    assert _main_code(tmp_path) == b"\x60"


_EXT_IN_IF = ".if 1 {\n    .extern br\n}\n"
_OWNER = '.import "ext"\n.alloc owner_block at 0x018000 {\n    .scope br {\n        K = 6\n        rts\n    }\n}\n'


def _build_scoped_user(root: Path, use: str) -> BuildResult:
    """An imported module's conditional `.extern br` must not turn the
    owner's own `.scope br` constants into link symbols for its importers."""
    (root / "ext.s").write_text(_EXT_IN_IF, encoding="utf-8")
    (root / "mod.s").write_text(_OWNER, encoding="utf-8")
    (root / "main.s").write_text('.import "mod"\n.alloc user_block at 0x018100 {\n' + use + "}\n", encoding="utf-8")
    return build_with_imports(root / "main.s", root / "out.ips", module_paths=[root], output_dir=root / "obj")


def test_a_conditional_extern_in_an_import_leaves_scoped_constants_constant(tmp_path: Path) -> None:
    result = _build_scoped_user(tmp_path, "    .for i := 0, br.K {\n        nop\n    }\n")
    assert (result.exit_code, _main_code(tmp_path)) == (0, b"\xea" * 6), result.diagnostics


def test_a_conditional_extern_in_an_import_leaves_scoped_immediates_folded(tmp_path: Path) -> None:
    result = _build_scoped_user(tmp_path, "    lda.b #br.K\n")
    assert (result.exit_code, _main_code(tmp_path)) == (0, b"\xa9\x06"), result.diagnostics
