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

import pytest

from a816.module_builder import ModuleBuilder
from a816.object_file import ObjectFile

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
    main.write_text("*= 0x008000\nmain:\n    lda #0x01\n    rts\n")
    _build(tmp_path, main)

    obj = _obj(tmp_path, "__main__")
    _set_mtime(main, _OLDER)
    _set_mtime(obj, _SENTINEL)

    _build(tmp_path, main)
    assert not _rebuilt(obj), "untouched module should stay cached"


def test_object_from_an_older_format_recompiles(tmp_path: Path) -> None:
    """A toolchain upgrade that bumps the object format must rebuild the cache,
    not fail reading it ("Unsupported version"): peers used to delete
    `build/obj` by hand before every build to get around it."""
    main = tmp_path / "main.s"
    main.write_text("*= 0x008000\nmain:\n    lda #0x01\n    rts\n")
    _build(tmp_path, main)

    obj = _obj(tmp_path, "__main__")
    data = bytearray(obj.read_bytes())
    data[4:6] = (ObjectFile.VERSION - 1).to_bytes(2, "little")  # header: magic u32, version u16
    obj.write_bytes(bytes(data))
    _set_mtime(main, _OLDER)
    _set_mtime(obj, _SENTINEL)

    _build(tmp_path, main)
    assert _rebuilt(obj), "an object of another format version must be rebuilt"
    assert ObjectFile.from_file(str(obj)).sections, "the rebuilt object reads back"


def test_a_cached_file_that_is_not_an_object_recompiles(tmp_path: Path) -> None:
    main = tmp_path / "main.s"
    main.write_text("*= 0x008000\nmain:\n    rts\n")
    _build(tmp_path, main)

    obj = _obj(tmp_path, "__main__")
    obj.write_bytes(b"junk")
    _set_mtime(main, _OLDER)
    _set_mtime(obj, _SENTINEL)

    _build(tmp_path, main)
    header = ObjectFile.read_header(str(obj))
    assert header is not None
    assert header.identity == ObjectFile.identity()


def test_a_touched_but_unchanged_source_stays_cached(tmp_path: Path) -> None:
    """Freshness follows content: a newer mtime with the same bytes (a checkout
    round trip, `touch`) is not a change."""
    main = tmp_path / "main.s"
    main.write_text("*= 0x008000\nmain:\n    lda #0x01\n    rts\n")
    _build(tmp_path, main)

    obj = _obj(tmp_path, "__main__")
    _set_mtime(obj, _SENTINEL)
    _set_mtime(main, _NEWER)

    _build(tmp_path, main)
    assert not _rebuilt(obj), "same bytes, newer mtime: the object must stay cached"


def test_edited_source_recompiles(tmp_path: Path) -> None:
    main = tmp_path / "main.s"
    main.write_text("*= 0x008000\nmain:\n    lda #0x01\n    rts\n")
    _build(tmp_path, main)

    obj = _obj(tmp_path, "__main__")
    _set_mtime(obj, _SENTINEL)
    main.write_text("*= 0x008000\nmain:\n    lda #0x02\n    rts\n")
    _set_mtime(main, _NEWER)

    _build(tmp_path, main)
    assert _rebuilt(obj), "edited source must invalidate the cache"


def test_edited_include_recompiles_dependent(tmp_path: Path) -> None:
    inc = tmp_path / "consts.s"
    inc.write_text("BAR = 0x7E0802\n")
    main = tmp_path / "main.s"
    main.write_text('.include "consts.s"\n*= 0x008000\nmain:\n    lda #0x01\n    rts\n')
    _build(tmp_path, main)

    obj = _obj(tmp_path, "__main__")
    _set_mtime(main, _OLDER)
    _set_mtime(obj, _SENTINEL)
    inc.write_text("BAR = 0x7E0803\n")  # only the include changed
    _set_mtime(inc, _NEWER)

    _build(tmp_path, main)
    assert _rebuilt(obj), "editing an .include'd file must invalidate the dependent"


def test_edited_incbin_asset_recompiles(tmp_path: Path) -> None:
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"\x01\x02\x03\x04")
    main = tmp_path / "main.s"
    main.write_text('*= 0x008000\n.incbin "blob.bin"\n')
    _build(tmp_path, main)

    obj = _obj(tmp_path, "__main__")
    _set_mtime(main, _OLDER)
    _set_mtime(obj, _SENTINEL)
    blob.write_bytes(b"\x01\x02\x03\x05")  # asset bytes changed
    _set_mtime(blob, _NEWER)

    _build(tmp_path, main)
    assert _rebuilt(obj), "editing an .incbin asset must invalidate the cache"


def test_edited_table_asset_recompiles(tmp_path: Path) -> None:
    tbl = tmp_path / "font.tbl"
    tbl.write_text("41=A\n42=B\n")
    main = tmp_path / "main.s"
    main.write_text('.table "font.tbl"\n*= 0x008000\n.text "AB"\n')
    _build(tmp_path, main)

    obj = _obj(tmp_path, "__main__")
    _set_mtime(main, _OLDER)
    _set_mtime(obj, _SENTINEL)
    tbl.write_text("41=B\n42=A\n")  # glyph mapping changed
    _set_mtime(tbl, _NEWER)

    _build(tmp_path, main)
    assert _rebuilt(obj), "editing a .table file must invalidate the cache"


def test_same_module_name_different_source_recompiles(tmp_path: Path) -> None:
    """A cached object built from a different source must not be reused.

    Distinct projects built from the same cwd map their entry file to the
    `__main__` object; the cache must key on the source identity, not just the
    name, or one build silently emits another's stale object.
    """
    obj_dir = tmp_path / "obj"

    first = tmp_path / "first.s"
    first.write_text("*= 0x008000\nmain:\n    lda #0x01\n    rts\n")
    ModuleBuilder(output_dir=obj_dir).build(first)
    obj = obj_dir / "__main__.o"
    _set_mtime(obj, _SENTINEL)

    # A different entry file, same `__main__` object name, older than the
    # cached object; only the source-identity guard forces a rebuild.
    second = tmp_path / "second.s"
    second.write_text("*= 0x008000\nother:\n    lda #0x02\n    rts\n")
    _set_mtime(second, _OLDER)
    ModuleBuilder(output_dir=obj_dir).build(second)
    assert _rebuilt(obj), "object built from a different source must rebuild"


def test_edited_import_recompiles_importer(tmp_path: Path) -> None:
    lib = tmp_path / "mylib.s"
    lib.write_text("*= 0x009000\nlib_func:\n    lda #0x01\n    rts\n")
    main = tmp_path / "main.s"
    main.write_text('.import "mylib"\n*= 0x008000\nmain:\n    jsr.w lib_func\n    rts\n')
    _build(tmp_path, main)

    main_obj = _obj(tmp_path, "__main__")
    lib_obj = _obj(tmp_path, "mylib")
    # main itself is unchanged; only the imported module's source moves.
    _set_mtime(main, _OLDER)
    _set_mtime(main_obj, _SENTINEL)
    _set_mtime(lib_obj, _SENTINEL)
    lib.write_text("*= 0x009000\nlib_func:\n    lda #0x02\n    rts\n")
    _set_mtime(lib, _NEWER)

    _build(tmp_path, main)
    assert _rebuilt(lib_obj), "edited import source must recompile the import itself"
    assert _rebuilt(main_obj), "edited import must propagate to the importer (baked constants)"


def _write_lib_and_main(tmpdir: Path, main_body: str) -> Path:
    (tmpdir / "lib.s").write_text("*= 0x009000\nlib_func:\n    rts\n")
    main = tmpdir / "main.s"
    main.write_text(main_body)
    return main


def _park_all(tmpdir: Path) -> None:
    """Sources in the past, objects at the sentinel: everything is fresh."""
    for src in tmpdir.glob("*.s"):
        _set_mtime(src, _OLDER)
    for obj in (tmpdir / "obj").glob("*.o"):
        _set_mtime(obj, _SENTINEL)


def test_warm_build_reuses_cached_imports_without_parsing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from a816.parse.mzparser import A816Parser

    main = _write_lib_and_main(tmp_path, '.import "lib"\n*= 0x008000\nmain:\n    jsr.w lib_func\n    rts\n')
    _build(tmp_path, main)
    _park_all(tmp_path)

    parsed: list[str] = []
    original = A816Parser.parse_as_ast

    def counting(program: str, filename: str = "memory.s", *args: object, **kwargs: object) -> object:
        parsed.append(filename)
        return original(program, filename, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(A816Parser, "parse_as_ast", staticmethod(counting))
    _build(tmp_path, main)
    assert parsed == [], "a fully cached build must rebuild the import graph from .deps"


def test_import_added_to_edited_module_is_discovered(tmp_path: Path) -> None:
    main = _write_lib_and_main(tmp_path, "*= 0x008000\nmain:\n    rts\n")
    _build(tmp_path, main)
    _park_all(tmp_path)

    main.write_text('.import "lib"\n*= 0x008000\nmain:\n    jsr.w lib_func\n    rts\n')
    _set_mtime(main, _NEWER)
    _build(tmp_path, main)
    assert _obj(tmp_path, "lib").exists(), "the new import must be discovered and compiled"


def test_import_added_through_edited_include_is_discovered(tmp_path: Path) -> None:
    inc = tmp_path / "imports.i"
    inc.write_text("; no imports yet\n")
    main = _write_lib_and_main(tmp_path, '.include "imports.i"\n*= 0x008000\nmain:\n    rts\n')
    _build(tmp_path, main)
    _park_all(tmp_path)
    _set_mtime(inc, _OLDER)

    inc.write_text('.import "lib"\n')
    _set_mtime(inc, _NEWER)
    _build(tmp_path, main)
    assert _obj(tmp_path, "lib").exists(), "an import added via an edited include must be discovered"


@pytest.mark.parametrize("sidecar", ["imports: lib\n/abs/path.s\n", '{"sidecar": 2}'])
def test_an_old_or_partial_sidecar_falls_back_to_parsing(tmp_path: Path, sidecar: str) -> None:
    """A sidecar from an older a816 (text lines) or missing keys is stale:
    imports are discovered by parsing and the module rebuilds."""
    main = _write_lib_and_main(tmp_path, '.import "lib"\n*= 0x008000\nmain:\n    jsr.w lib_func\n    rts\n')
    _build(tmp_path, main)
    _obj(tmp_path, "__main__").with_suffix(".deps").write_text(sidecar)
    _park_all(tmp_path)
    (tmp_path / "obj" / "lib.o").unlink()

    _build(tmp_path, main)
    assert _obj(tmp_path, "lib").exists(), "imports must still be discovered by parsing"
    assert _rebuilt(_obj(tmp_path, "__main__"))


# --- inputs the mtime cache missed (probed on a40) ---


def _value_byte(tmp_path: Path) -> int:
    """The byte the build placed at $00:8000, from the main object."""
    obj = ObjectFile.from_file(str(_obj(tmp_path, "__main__")))
    return obj.sections[-1].code[0]


def test_changing_a_define_recompiles(tmp_path: Path) -> None:
    main = tmp_path / "main.s"
    main.write_text("*= 0x008000\n    .db VALUE\n")
    obj_dir = tmp_path / "obj"
    ModuleBuilder(output_dir=obj_dir, symbols={"VALUE": 1}).build(main)
    ModuleBuilder(output_dir=obj_dir, symbols={"VALUE": 2}).build(main)
    assert _value_byte(tmp_path) == 2


def test_a_new_include_earlier_on_the_search_path_recompiles(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "value.i").write_text("VALUE = 1\n")
    main = tmp_path / "main.s"
    main.write_text('.include "value.i"\n*= 0x008000\n    .db VALUE\n')
    search = [tmp_path / "a", tmp_path / "b"]
    ModuleBuilder(output_dir=tmp_path / "obj", include_paths=search).build(main)

    (tmp_path / "a" / "value.i").write_text("VALUE = 2\n")  # shadows b/value.i from now on
    ModuleBuilder(output_dir=tmp_path / "obj", include_paths=search).build(main)
    assert _value_byte(tmp_path) == 2


def test_a_new_asset_earlier_on_the_search_path_recompiles(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "blob.bin").write_bytes(b"\x01")
    main = tmp_path / "main.s"
    main.write_text('*= 0x008000\n.incbin "blob.bin"\n')
    search = [tmp_path / "a", tmp_path / "b"]
    ModuleBuilder(output_dir=tmp_path / "obj", include_paths=search).build(main)

    (tmp_path / "a" / "blob.bin").write_bytes(b"\x02")
    ModuleBuilder(output_dir=tmp_path / "obj", include_paths=search).build(main)
    assert _value_byte(tmp_path) == 2


def test_a_file_replaced_with_an_older_mtime_recompiles(tmp_path: Path) -> None:
    """`rsync -a`, `cp -p`, `tar x`: new bytes, old mtime."""
    main = tmp_path / "main.s"
    main.write_text("*= 0x008000\n    .db 1\n")
    _build(tmp_path, main)

    main.write_text("*= 0x008000\n    .db 2\n")
    _set_mtime(main, _OLDER)  # older than the object
    _build(tmp_path, main)
    assert _value_byte(tmp_path) == 2


def test_changing_the_include_paths_recompiles(tmp_path: Path) -> None:
    main = tmp_path / "main.s"
    main.write_text("*= 0x008000\n    .db 1\n")
    ModuleBuilder(output_dir=tmp_path / "obj").build(main)
    obj = _obj(tmp_path, "__main__")
    _set_mtime(main, _OLDER)
    _set_mtime(obj, _SENTINEL)

    ModuleBuilder(output_dir=tmp_path / "obj", include_paths=[tmp_path]).build(main)
    assert _rebuilt(obj)


def test_no_cache_compiles_every_module(tmp_path: Path) -> None:
    main = tmp_path / "main.s"
    main.write_text("*= 0x008000\n    .db 1\n")
    _build(tmp_path, main)
    obj = _obj(tmp_path, "__main__")
    _set_mtime(main, _OLDER)
    _set_mtime(obj, _SENTINEL)

    ModuleBuilder(module_paths=[tmp_path], output_dir=tmp_path / "obj", use_cache=False).build(main)
    assert _rebuilt(obj)


def test_an_import_whose_inputs_are_unchanged_keeps_its_importer_cached(tmp_path: Path) -> None:
    main = _write_lib_and_main(tmp_path, '.import "lib"\n*= 0x008000\nmain:\n    jsr.w lib_func\n    rts\n')
    _build(tmp_path, main)
    _park_all(tmp_path)

    _build(tmp_path, main)
    assert not _rebuilt(_obj(tmp_path, "__main__"))
    assert not _rebuilt(_obj(tmp_path, "lib"))
