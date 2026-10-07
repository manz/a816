"""Behaviour pins for the object-mode `.import` caches.

Every module of a build imports the same core modules, and each import
used to decode the imported `.o` and re-parse its source again, once per
importer: a cold build grew quadratically with the module count. These
tests pin that both are read once, that a rewritten `.o` is noticed, and
that the import plan worked out from a parse is reused.
"""

from __future__ import annotations

import os
from pathlib import Path

from a816.build_inputs import recording_misses
from a816.module_builder import ModuleBuilder
from a816.parse.codegen.modules import (
    ParsedImport,
    _import_view,
    _parse_import,
    _record_imported_reservations,
)
from a816.parse.mzparser import A816Parser
from a816.symbols import Resolver


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


def test_the_build_parses_each_import_once(tmp_path: Path) -> None:
    builder = ModuleBuilder(module_paths=[tmp_path], include_paths=[tmp_path], output_dir=tmp_path / "obj")

    builder.build(_write_modules(tmp_path, "first:\n    rts\n"))

    assert list(builder._import_asts) == [str(tmp_path / "core.s")]


def test_a_cached_parse_is_served_without_reading(tmp_path: Path) -> None:
    resolver = Resolver()
    missing = tmp_path / "gone.s"
    parsed = ParsedImport(A816Parser.parse_as_ast("X = 1\n", str(missing)), set())
    resolver.context.import_asts = {str(missing): parsed}

    assert _parse_import(missing, resolver) is parsed


def test_a_cached_parse_replays_its_misses(tmp_path: Path) -> None:
    """The importer's recorded inputs must match a fresh parse's."""
    resolver = Resolver()
    source = tmp_path / "core.s"
    parsed = ParsedImport(A816Parser.parse_as_ast("X = 1\n", str(source)), {"/probe/missing.i"})
    resolver.context.import_asts = {str(source): parsed}

    with recording_misses() as misses:
        _parse_import(source, resolver)

    assert misses == {"/probe/missing.i"}


def test_a_fresh_parse_fills_the_cache(tmp_path: Path) -> None:
    resolver = Resolver()
    resolver.context.import_asts = {}
    source = tmp_path / "core.s"
    source.write_text("X = 1\n", encoding="utf-8")

    parsed = _parse_import(source, resolver)

    assert resolver.context.import_asts[str(source)] is parsed


def test_without_a_cache_each_import_parses_afresh(tmp_path: Path) -> None:
    resolver = Resolver()
    source = tmp_path / "core.s"
    source.write_text("X = 1\n", encoding="utf-8")

    assert _parse_import(source, resolver) is not _parse_import(source, resolver)


def test_an_unreadable_import_is_none(tmp_path: Path) -> None:
    assert _parse_import(tmp_path / "gone.s", Resolver()) is None


def _plan_of(source: Path) -> ParsedImport:
    parsed = _parse_import(source, Resolver())
    assert parsed is not None
    return parsed


def test_the_import_plan_is_worked_out_once(tmp_path: Path) -> None:
    source = tmp_path / "core.s"
    source.write_text("X = 1\n", encoding="utf-8")
    parsed = _plan_of(source)

    assert parsed.object_mode_plan() is parsed.object_mode_plan()


def test_the_plan_lists_public_reservations(tmp_path: Path) -> None:
    source = tmp_path / "core.s"
    source.write_text(
        ".struct Actor {\n    word hp\n}\n"
        ".reserve hero as Actor in wram\n"
        ".reserve buffer 0x10 in wram\n"
        ".reserve _scratch 0x04 in wram\n",
        encoding="utf-8",
    )

    plan = _plan_of(source).object_mode_plan()

    assert plan.reservations == (("hero", "Actor"), ("buffer", None))


def test_a_replayed_plan_sizes_typed_reservations_per_importer() -> None:
    """The struct size comes from the importer's resolver, not the plan."""
    resolver = Resolver()
    resolver.struct_sizes["Actor"] = 2

    _record_imported_reservations((("hero", "Actor"), ("buffer", None)), resolver)

    assert resolver.reservation_sizes == {"hero": 2, "buffer": None}
