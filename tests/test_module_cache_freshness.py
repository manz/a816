"""Incremental-build freshness: a cached `.o` must rebuild when anything it
was built from changes: the module source, an `.include`d file, an
`.incbin` / `.table` asset, or an imported module whose constants it baked in.

Recompilation is detected with an mtime sentinel: the object's mtime is parked
at a fixed past value before the second build, so a rebuild (which rewrites the
object to wall-clock now) is observable as "mtime moved off the sentinel."
"""

from __future__ import annotations

import os
from pathlib import Path

from a816.module_builder import ModuleBuilder

# Parked-in-the-past object mtime; a rebuild moves it to wall-clock now.
_SENTINEL = 1_000_000_000  # 2001-09-09
_OLDER = _SENTINEL - 100
_NEWER = _SENTINEL + 100


def _set_mtime(path: Path, when: int) -> None:
    os.utime(path, (when, when))


def _build(tmpdir: Path, main: Path) -> None:
    # Fresh builder each build: `_discovered` would otherwise short-circuit.
    ModuleBuilder(module_paths=[tmpdir], include_paths=[tmpdir], output_dir=tmpdir / "obj").build(main)


def _obj(tmpdir: Path, module: str) -> Path:
    return tmpdir / "obj" / f"{module}.o"


def _rebuilt(obj: Path) -> bool:
    """True when the object moved off its parked sentinel mtime."""
    return obj.stat().st_mtime != _SENTINEL


def test_unchanged_module_is_not_recompiled(tmp_path: Path) -> None:
    main = tmp_path / "main.s"
    main.write_text("main:\n    lda #0x01\n    rts\n")
    _build(tmp_path, main)

    obj = _obj(tmp_path, "__main__")
    _set_mtime(main, _OLDER)
    _set_mtime(obj, _SENTINEL)

    _build(tmp_path, main)
    assert not _rebuilt(obj), "untouched module should stay cached"


def test_edited_source_recompiles(tmp_path: Path) -> None:
    main = tmp_path / "main.s"
    main.write_text("main:\n    lda #0x01\n    rts\n")
    _build(tmp_path, main)

    obj = _obj(tmp_path, "__main__")
    _set_mtime(obj, _SENTINEL)
    _set_mtime(main, _NEWER)  # source newer than object

    _build(tmp_path, main)
    assert _rebuilt(obj), "edited source must invalidate the cache"


def test_edited_include_recompiles_dependent(tmp_path: Path) -> None:
    inc = tmp_path / "consts.s"
    inc.write_text("BAR = 0x7E0802\n")
    main = tmp_path / "main.s"
    main.write_text('.include "consts.s"\nmain:\n    lda #0x01\n    rts\n')
    _build(tmp_path, main)

    obj = _obj(tmp_path, "__main__")
    _set_mtime(main, _OLDER)
    _set_mtime(obj, _SENTINEL)
    _set_mtime(inc, _NEWER)  # only the include changed

    _build(tmp_path, main)
    assert _rebuilt(obj), "editing an .include'd file must invalidate the dependent"


def test_edited_incbin_asset_recompiles(tmp_path: Path) -> None:
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"\x01\x02\x03\x04")
    main = tmp_path / "main.s"
    main.write_text('.incbin "blob.bin"\n')
    _build(tmp_path, main)

    obj = _obj(tmp_path, "__main__")
    _set_mtime(main, _OLDER)
    _set_mtime(obj, _SENTINEL)
    _set_mtime(blob, _NEWER)  # asset bytes changed

    _build(tmp_path, main)
    assert _rebuilt(obj), "editing an .incbin asset must invalidate the cache"


def test_edited_table_asset_recompiles(tmp_path: Path) -> None:
    tbl = tmp_path / "font.tbl"
    tbl.write_text("41=A\n42=B\n")
    main = tmp_path / "main.s"
    main.write_text('.table "font.tbl"\n.text "AB"\n')
    _build(tmp_path, main)

    obj = _obj(tmp_path, "__main__")
    _set_mtime(main, _OLDER)
    _set_mtime(obj, _SENTINEL)
    _set_mtime(tbl, _NEWER)  # glyph mapping changed

    _build(tmp_path, main)
    assert _rebuilt(obj), "editing a .table file must invalidate the cache"


def test_edited_import_recompiles_importer(tmp_path: Path) -> None:
    lib = tmp_path / "mylib.s"
    lib.write_text("lib_func:\n    lda #0x01\n    rts\n")
    main = tmp_path / "main.s"
    main.write_text('.import "mylib"\nmain:\n    jsr.w lib_func\n    rts\n')
    _build(tmp_path, main)

    main_obj = _obj(tmp_path, "__main__")
    lib_obj = _obj(tmp_path, "mylib")
    # main itself is unchanged; only the imported module's source moves.
    _set_mtime(main, _OLDER)
    _set_mtime(main_obj, _SENTINEL)
    _set_mtime(lib_obj, _SENTINEL)
    _set_mtime(lib, _NEWER)

    _build(tmp_path, main)
    assert _rebuilt(lib_obj), "edited import source must recompile the import itself"
    assert _rebuilt(main_obj), "edited import must propagate to the importer (baked constants)"
