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
