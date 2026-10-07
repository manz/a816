"""Behaviour pins for the object-mode `.import` caches.

Every module of a build imports the same core modules, and each import
used to decode the imported `.o` again, once per importer: a cold build
grew quadratically with the module count. These tests pin that it is
read once, and that a rewritten `.o` is noticed.
"""

from __future__ import annotations

import os
from pathlib import Path

from a816.module_builder import ModuleBuilder
from a816.parse.codegen.modules import _import_view


def _build(root: Path, main: Path) -> None:
    ModuleBuilder(module_paths=[root], include_paths=[root], output_dir=root / "obj").build(main)


def _write_modules(root: Path, core_symbols: str) -> Path:
    (root / "core.s").write_text(f".alloc core_code at 0x008000 {{\n{core_symbols}}}\n", encoding="utf-8")
    main = root / "main.s"
    main.write_text('.import "core"\n.alloc main_code at 0x009000 {\nmain:\n    jsr.w first\n    rts\n}\n')
    return main


def _bump_mtime(path: Path) -> None:
    stamp = path.stat()
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 1_000_000))


def test_one_object_version_is_decoded_once(tmp_path: Path) -> None:
    _build(tmp_path, _write_modules(tmp_path, "first:\n    rts\n"))
    obj = tmp_path / "obj" / "core.o"

    assert _import_view(obj) is _import_view(obj)


def test_a_rebuilt_object_is_decoded_again(tmp_path: Path) -> None:
    """A stale view would hand importers the old symbols."""
    main = _write_modules(tmp_path, "first:\n    rts\n")
    _build(tmp_path, main)
    obj = tmp_path / "obj" / "core.o"
    before = _import_view(obj)

    _write_modules(tmp_path, "first:\n    rts\nsecond:\n    rts\n")
    _bump_mtime(tmp_path / "core.s")
    _build(tmp_path, main)

    assert "second" in _import_view(obj).provided_names
    assert "second" not in before.provided_names
