"""bss reservations from different pools may not share memory, except between
contexts a pool declares mutually exclusive (`contexts A, B`).

Emitted bytes already meet the writer's overlap check; bss reservations emit
nothing, so before this check two bss pools over the same memory linked
silently: the hand-assigned-WRAM collision class bss pools exist to end.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from a816.exceptions import PoolOverlapLinkError
from a816.formatter import A816Formatter
from a816.linker import Linker
from a816.object_file import ObjectFile, PoolAlloc, PoolDecl
from a816.parse.mzparser import A816Parser
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
