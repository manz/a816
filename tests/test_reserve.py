"""`.res N` reservation + byte-less (`bss`) pools for WRAM/RAM layout.

`.res` advances the PC without emitting bytes. A `bss` pool reserves and
overlap-checks address space (WRAM, SRAM, custom RAM maps) but writes nothing
into the image, and rejects any attempt to emit bytes into it. Exercised
through the real build path (object compilation + linking), not the
deprecated direct mode.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from a816.linker import Linker
from a816.object_file import ObjectFile
from a816.program import Program

# A writable WRAM map + a bss pool over bank $7E. `code` is a normal ROM pool.
_PREAMBLE = """
.map identifier=1 bank_range=0xc0, 0xfd addr_range=0x0000, 0xffff mask=0x10000 mirror_bank_range=0x40, 0x7d
.map identifier=3 bank_range=0x7e, 0x7f addr_range=0x0000, 0xffff mask=0x10000 writable=1
.pool wram { bss  range 0x7e0000 0x7e1fff  strategy order }
.pool code { range 0xc10000 0xc1ffff  strategy order }
"""


def _link(src: str, tmpdir: str) -> ObjectFile:
    asm = Path(tmpdir) / "m.s"
    asm.write_text(_PREAMBLE + src)
    obj = Path(tmpdir) / "m.o"
    assert Program().assemble_as_object(str(asm), obj) == 0
    return Linker([ObjectFile.from_file(str(obj))]).link(base_address=0x8000)


def _symbols(linked: ObjectFile) -> dict[str, int]:
    return {name: value for name, value, *_ in linked.symbols}


def test_bss_pool_survives_cross_module_import() -> None:
    """Regression: a `bss` pool declared in an *imported* module must build.

    The importer both inlines the preamble source (a fresh decl, bss=True)
    AND reads the preamble's compiled `.o` (an extern decl). If the object
    format drops `bss`, the `.o` copy deserializes as bss=False and the two
    collide on `generate_pool`'s shape-check ('already declared with
    different shape'). A same-file decl never exercises this - only the
    import path does. Pin the round-trip through compile + link.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "preamble.s").write_text(
            ".map identifier=3 bank_range=0x7e, 0x7f addr_range=0x0000, 0xffff mask=0x10000 writable=1\n"
            ".map identifier=1 bank_range=0xc0, 0xfd addr_range=0x0000, 0xffff mask=0x10000 mirror_bank_range=0x40, 0x7d\n"
            ".pool wram { bss  range 0x7e0000 0x7e1fff  strategy order }\n"
            ".pool code { range 0xc10000 0xc1ffff  strategy order }\n"
            ".reserve blob 0x10 in wram\n"
        )
        (tmp / "main.s").write_text('.import "preamble"\n.alloc main in code {\n    lda.l blob\n    rts\n}\n')
        # Compile the imported module first (carries the bss PoolDecl + the
        # reservation), then the consumer that imports it.
        pre_o = tmp / "preamble.o"
        assert Program().assemble_as_object(str(tmp / "preamble.s"), pre_o) == 0
        main_program = Program()
        main_program.add_module_path(tmp)
        main_o = tmp / "main.o"
        # Before the fix this raised 'pool wram already declared with different
        # shape' at the importer's compile (inline bss=True vs imported-.o
        # bss=False).
        assert main_program.assemble_as_object(str(tmp / "main.s"), main_o) == 0
        linked = Linker([ObjectFile.from_file(str(pre_o)), ObjectFile.from_file(str(main_o))]).link(base_address=0x8000)
        # Reserved base label resolves to the bss pool's range start.
        assert _symbols(linked)["blob"] == 0x7E0000


def test_bss_alloc_binds_symbols_without_emitting() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        linked = _link(
            """
.alloc in wram {
foo:
    .res 2
bar:
    .res 1
baz:
    .res 0x100
}
""",
            tmp,
        )
        syms = _symbols(linked)
        assert syms["foo"] == 0x7E0000
        assert syms["bar"] == 0x7E0002  # foo + 2
        assert syms["baz"] == 0x7E0003  # bar + 1
        # The bss section survives placement but carries no bytes.
        bss = [s for s in linked.sections if s.bss]
        assert len(bss) == 1
        assert bss[0].code == b""


def test_code_references_resolve_to_reserved_wram_addresses() -> None:
    """A ROM routine that touches reserved WRAM gets the allocator-assigned
    addresses, and the WRAM bytes never land in the image."""
    with tempfile.TemporaryDirectory() as tmp:
        linked = _link(
            """
.alloc in wram {
hp:
    .res 1
}
.alloc r in code {
    lda.w hp
    rts
}
""",
            tmp,
        )
        code = next(s for s in linked.sections if s.code)
        # lda.w $0000 (low word of $7E0000), rts. hp resolved to the WRAM slot.
        assert code.code == b"\xad\x00\x00\x60"
        # No section carrying WRAM bytes.
        assert all(s.code == b"" for s in linked.sections if s.bss)


def test_flat_reserve_binds_symbols() -> None:
    """`.reserve NAME SIZE in POOL` lays out RAM vars flat, no wrapper alloc."""
    with tempfile.TemporaryDirectory() as tmp:
        asm = Path(tmp) / "m.s"
        asm.write_text(
            _PREAMBLE
            + ".reserve player_x  0x2   in wram\n"
            + ".reserve player_hp 0x1   in wram\n"
            + ".reserve scratch   0x100 in wram\n"
        )
        obj = Path(tmp) / "m.o"
        assert Program().assemble_as_object(str(asm), obj) == 0
        linked = Linker([ObjectFile.from_file(str(obj))]).link(base_address=0x8000)
        syms = _symbols(linked)
        assert syms["player_x"] == 0x7E0000
        assert syms["player_hp"] == 0x7E0002  # player_x + 2
        assert syms["scratch"] == 0x7E0003  # player_hp + 1
        assert all(s.code == b"" for s in linked.sections if s.bss)


_STRUCT = """
.struct GameState {
    word score
    byte lives
    byte level
}
"""


def test_typed_reserve_publishes_struct_fields() -> None:
    """`.reserve NAME as TYPE in POOL` reserves sizeof(TYPE) and binds NAME +
    NAME.<field> at the allocator-assigned address."""
    with tempfile.TemporaryDirectory() as tmp:
        asm = Path(tmp) / "m.s"
        asm.write_text(_PREAMBLE + _STRUCT + ".reserve game_state as GameState in wram\n.reserve after 0x1 in wram\n")
        obj = Path(tmp) / "m.o"
        assert Program().assemble_as_object(str(asm), obj) == 0
        linked = Linker([ObjectFile.from_file(str(obj))]).link(base_address=0x8000)
        syms = _symbols(linked)
        assert syms["game_state"] == 0x7E0000
        assert syms["game_state.score"] == 0x7E0000  # offset 0
        assert syms["game_state.lives"] == 0x7E0002  # offset 2 (word + )
        assert syms["game_state.level"] == 0x7E0003  # offset 3
        # The next reservation lands past the whole 4-byte struct.
        assert syms["after"] == 0x7E0004
        assert all(s.code == b"" for s in linked.sections if s.bss)


def test_typed_reserve_unknown_struct_errors() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        asm = Path(tmp) / "m.s"
        asm.write_text(_PREAMBLE + ".reserve x as NoSuchStruct in wram\n")
        assert Program().assemble_as_object(str(asm), Path(tmp) / "m.o") != 0


def test_byte_less_alloc_survives_in_any_pool() -> None:
    """A byte-less body (a `.reserve`, or a label-only `.alloc at` entry marker)
    must place + bind + emit nothing, without crashing the object serializer,
    in a bss pool OR a plain pool. Byte-less sections survive universally; only
    the bss flag adds the reject-emitted-bytes guard."""
    with tempfile.TemporaryDirectory() as tmp:
        asm = Path(tmp) / "m.s"
        # reserve into a NON-bss pool (reserves a gap) + a label-only pinned alloc.
        asm.write_text(
            ".pool rom { range 0xc10000 0xc1ffff }\n.reserve gap 0x10 in rom\n.alloc at 0xc18000 { entry_marker: }\n"
        )
        obj = Path(tmp) / "m.o"
        assert Program().assemble_as_object(str(asm), obj) == 0
        names = {n for n, *_ in ObjectFile.from_file(str(obj)).symbols}
        assert "gap" in names
        assert "entry_marker" in names


def test_bss_pool_rejects_emitted_bytes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        asm = Path(tmp) / "m.s"
        asm.write_text(_PREAMBLE + ".alloc in wram {\nx:\n    .db 0x42\n}\n")
        # Reported as a non-zero exit by assemble_as_object (NodeError is caught).
        assert Program().assemble_as_object(str(asm), Path(tmp) / "m.o") != 0


def test_negative_reserve_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        asm = Path(tmp) / "m.s"
        asm.write_text(_PREAMBLE + ".alloc in wram {\n    .res -1\n}\n")
        assert Program().assemble_as_object(str(asm), Path(tmp) / "m.o") != 0


def test_bss_and_reserve_round_trip_canonical() -> None:
    """`.pool bss` and `.res` reproduce in canonical output (format stability)."""
    from a816.parse.mzparser import A816Parser

    pool = A816Parser.parse_as_ast(".pool wram { bss  range 0x7e0000 0x7e1fff }\n", "t.s").nodes[0]
    canonical = pool.to_canonical()
    assert "bss" in canonical
    assert canonical.startswith(".pool wram {")

    res = A816Parser.parse_as_ast(".res 0x10\n", "t.s").nodes[0]
    assert res.to_canonical() == ".res 0x10"
    assert res.to_representation()[0] == "res"

    typed = A816Parser.parse_as_ast(".reserve game_state as GameState in wram\n", "t.s").nodes[0]
    assert typed.to_canonical() == ".reserve game_state as GameState in wram"
    assert typed.to_representation() == ("reserve_typed", "game_state", "GameState", "wram")
