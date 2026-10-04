"""bss reservations from different pools may not share memory, except between
contexts a pool declares mutually exclusive (`contexts A, B`).

Emitted bytes already meet the writer's overlap check; bss reservations emit
nothing, so before this check two bss pools over the same memory linked
silently: the hand-assigned-WRAM collision class bss pools exist to end.

Also: labels bound in a pool alloc ride that alloc's section. The linker used
to find the section by sandbox address, which contexts make ambiguous and
which missed end-of-body labels altogether.
"""

from __future__ import annotations

import tempfile
from dataclasses import fields
from pathlib import Path

import pytest

from a816.exceptions import PoolOverlapLinkError
from a816.formatter import A816Formatter
from a816.linker import Linker
from a816.object_file import ObjectFile, PoolAlloc, PoolDecl
from a816.parse.mzparser import A816Parser
from a816.pool import Pool, PoolOverlapError
from a816.program import Program
from tests.test_reserve import _PREAMBLE, _link, _symbols

_MENU_RAM = ".pool menu_ram { bss  range 0x7e9800 0x7e990f  contexts field_menu, treasure, battle }\n"


def _link_src(src: str) -> dict[str, int]:
    with tempfile.TemporaryDirectory() as tmp:
        return _symbols(_link(src, tmp))


def test_accidental_overlap_between_bss_pools_is_rejected() -> None:
    src = """
.pool scratch { bss  range 0x7e0010 0x7e001f  strategy order }
.reserve player 0x20 in wram
.reserve tmp 0x10 in scratch
"""
    with pytest.raises(PoolOverlapLinkError, match="`player` in pool `wram`.*`tmp` in pool `scratch`"):
        _link_src(src)


def test_contexts_of_one_pool_share_its_memory() -> None:
    syms = _link_src(
        _MENU_RAM
        + """
.reserve field_hdma 0x40 in menu_ram.field_menu
.reserve treasure_hdma 0x40 in menu_ram.treasure
.reserve text_ring 0x100 in menu_ram.battle
"""
    )
    assert syms["field_hdma"] == syms["treasure_hdma"] == syms["text_ring"] == 0x7E9800


def test_one_context_still_packs_its_own_reservations() -> None:
    syms = _link_src(
        _MENU_RAM
        + """
.reserve field_hdma 0x40 in menu_ram.field_menu
.reserve field_shadow 0x40 in menu_ram.field_menu
"""
    )
    assert syms["field_shadow"] == syms["field_hdma"] + 0x40


def test_contexts_of_different_pools_do_not_share() -> None:
    src = (
        _MENU_RAM
        + """
.pool other { bss  range 0x7e9800 0x7e98ff  contexts a, b }
.reserve field_hdma 0x10 in menu_ram.field_menu
.reserve x 0x10 in other.a
"""
    )
    with pytest.raises(PoolOverlapLinkError):
        _link_src(src)


def test_reserving_in_the_pool_itself_overlaps_its_contexts() -> None:
    src = (
        _MENU_RAM
        + """
.reserve always 0x10 in menu_ram
.reserve field_hdma 0x10 in menu_ram.field_menu
"""
    )
    with pytest.raises(PoolOverlapLinkError):
        _link_src(src)


def test_window_pool_without_reservations_may_cover_others() -> None:
    syms = _link_src(
        """
.pool clear_window { bss  range 0x7e0000 0x7e00ff  strategy order }
.reserve timers 0x10 in wram
"""
    )
    assert syms["timers"] == 0x7E0000


def test_contexts_require_bss() -> None:
    result = A816Parser.parse_as_ast(".pool rom { range 0xc18000 0xc180ff  contexts a, b }\n", filename="t.s")
    assert result.parse_error is not None
    assert "has contexts but is not `bss`" in str(result.parse_error)


def test_overlap_error_names_both_reservations_sources() -> None:
    src = """
.pool scratch { bss  range 0x7e0010 0x7e001f  strategy order }
.reserve player 0x20 in wram
.reserve tmp 0x10 in scratch
"""
    with pytest.raises(PoolOverlapLinkError) as excinfo:
        _link_src(src)
    first, second = excinfo.value.clashes[0]
    assert (first.source.rsplit(":", 1)[1], second.source.rsplit(":", 1)[1]) == ("8", "9")
    assert "E0406" in excinfo.value.format()


def test_redeclaring_a_pool_with_other_contexts_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        asm = Path(tmp) / "m.s"
        asm.write_text(_PREAMBLE + _MENU_RAM + _MENU_RAM.replace("battle", "title"))
        assert Program().assemble_as_object(str(asm), Path(tmp) / "m.o") != 0


def test_modules_must_agree_on_a_pools_contexts() -> None:
    def module(contexts: list[str]) -> ObjectFile:
        decls = [PoolDecl(name="ram", ranges=[(0x7E9800, 0x7E98FF)], fill=0, strategy="order", bss=True)]
        decls += [
            PoolDecl(name=f"ram.{c}", ranges=[(0x7E9800, 0x7E98FF)], fill=0, strategy="order", bss=True, context=c)
            for c in contexts
        ]
        return ObjectFile([], [], pool_decls=decls)

    linker = Linker([module(["a", "b"]), module(["a", "c"])])
    with pytest.raises(ValueError, match="conflicting contexts"):
        linker.link()


def test_context_and_source_round_trip_through_the_object_file(tmp_path: Path) -> None:
    obj = ObjectFile(
        [],
        [],
        pool_decls=[
            PoolDecl(name="ram.a", ranges=[(0x7E0000, 0x7E00FF)], fill=0, strategy="order", bss=True, context="a")
        ],
        pool_allocs=[PoolAlloc(pool_name="ram.a", symbol_name="x", section_idx=0, size=4, source="m.s:3")],
    )
    obj.write(str(tmp_path / "m.o"))
    back = ObjectFile.from_file(str(tmp_path / "m.o"))
    assert (back.pool_decls[0].context, back.pool_allocs[0].source) == ("a", "m.s:3")


def test_formatter_keeps_contexts() -> None:
    assert "    contexts field_menu, treasure, battle\n" in A816Formatter().format_text(_MENU_RAM)


def test_pinned_reservations_overlapping_inside_one_context_are_rejected() -> None:
    src = (
        _MENU_RAM
        + """
.reserve a 0x40 at 0x7e9800 in menu_ram.field_menu
.reserve b 2 at 0x7e9802 in menu_ram.field_menu
"""
    )
    with pytest.raises(PoolOverlapError, match="pinned alloc 'b'"):
        _link_src(src)


def test_pinned_reservations_in_two_contexts_may_overlap() -> None:
    syms = _link_src(
        _MENU_RAM
        + """
.reserve a 0x40 at 0x7e9800 in menu_ram.field_menu
.reserve b 2 at 0x7e9802 in menu_ram.treasure
"""
    )
    assert (syms["a"], syms["b"]) == (0x7E9800, 0x7E9802)


def _compile_pair(tmp: Path, preamble: str, main: str) -> list[ObjectFile]:
    """Compile `preamble.s`, then a `main.s` that imports it; return both objects."""
    (tmp / "preamble.s").write_text(_PREAMBLE + preamble)
    (tmp / "main.s").write_text('.import "preamble"\n' + main)
    assert Program().assemble_as_object(str(tmp / "preamble.s"), tmp / "preamble.o") == 0
    importer = Program()
    importer.add_module_path(tmp)
    assert importer.assemble_as_object(str(tmp / "main.s"), tmp / "main.o") == 0
    return [ObjectFile.from_file(str(tmp / "preamble.o")), ObjectFile.from_file(str(tmp / "main.o"))]


def test_contexts_survive_an_import() -> None:
    """Regression: an imported `POOL.CTX` decl lost its context, so the importer
    saw the pool without contexts and failed 'already declared with different shape'."""
    with tempfile.TemporaryDirectory() as tmpdir:
        objects = _compile_pair(
            Path(tmpdir),
            _MENU_RAM + ".reserve field_hdma 0x40 in menu_ram.field_menu\n",
            ".reserve treasure_hdma 0x40 in menu_ram.treasure\n",
        )
        syms = _symbols(Linker(objects).link(base_address=0x8000))
    assert syms["field_hdma"] == syms["treasure_hdma"] == 0x7E9800


def test_pinned_reservations_overlapping_in_one_context_across_modules_are_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        objects = _compile_pair(
            Path(tmpdir),
            _MENU_RAM + ".reserve a 0x40 at 0x7e9800 in menu_ram.field_menu\n",
            ".reserve b 2 at 0x7e9802 in menu_ram.field_menu\n",
        )
        linker = Linker(objects)
        with pytest.raises(PoolOverlapError, match="pinned alloc 'b'"):
            linker.link(base_address=0x8000)


def test_body_labels_in_contexts_land_in_their_own_section() -> None:
    """Both bodies sit at sandbox $7E9800 in main.o; only `f1` moves (behind the
    preamble's 0x40 in the same context). Sections found by sandbox address
    gave `f1` the treasure section's delta."""
    with tempfile.TemporaryDirectory() as tmpdir:
        objects = _compile_pair(
            Path(tmpdir),
            _MENU_RAM + ".reserve field_hdma 0x40 in menu_ram.field_menu\n",
            ".alloc in menu_ram.treasure {\nt1:\n    .res 2\n}\n.alloc in menu_ram.field_menu {\nf1:\n    .res 2\n}\n",
        )
        syms = _symbols(Linker(objects).link(base_address=0x8000))
    assert (syms["t1"], syms["f1"]) == (0x7E9800, 0x7E9840)


def test_end_marker_label_rides_its_alloc_section() -> None:
    """A label right after the body's last byte sits one past the section's span,
    so the address lookup missed it and gave it the module delta: ff4's
    `gils_window_tilemap_4_end` landed before its start and a length operand
    computed 45528 instead of 152."""
    with tempfile.TemporaryDirectory() as tmpdir:
        objects = _compile_pair(
            Path(tmpdir),
            ".alloc first in code {\n    .db 0, 0, 0, 0\n}\n",
            ".alloc table in code {\n    .db 1, 2, 3\ntable_end:\n}\n",
        )
        syms = _symbols(Linker(objects).link(base_address=0x8000))
    assert syms["table_end"] - syms["table"] == 3


def test_pool_from_decl_carries_every_decl_field() -> None:
    """Every `PoolDecl` field must survive `Pool.from_decl`: three hand-copied
    conversions each dropped a field (`bss`, then `context`) before there was one."""
    decl = PoolDecl(name="ram.a", ranges=[(0x7E0000, 0x7E00FF)], fill=0xEA, strategy="order", bss=True, context="a")
    pool = Pool.from_decl(decl)
    rebuilt = {
        "name": pool.name,
        "ranges": [(r.start, r.end) for r in pool.ranges],
        "fill": pool.fill,
        "strategy": pool.strategy.value,
        "bss": pool.bss,
        "context": pool.context,
    }
    assert rebuilt == {f.name: getattr(decl, f.name) for f in fields(PoolDecl)}
