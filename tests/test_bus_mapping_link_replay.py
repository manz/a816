"""`.map` directives must survive compile -> link.

Before this lived in the format, `.map` configured the compiler's
resolver bus at codegen time but the linker spun up a fresh Program
with the default bus. Custom cartridge mappings (SA-1, ExHiROM,
anything beyond low_rom) silently disappeared and downstream
addresses resolved against the wrong bus.

The fix: serialize `BusMapping` entries into the `.o`, the linker
collects + dedupes them (paired-import re-emits the same `.map` in
every consumer's `.o`), and `Program.import_linked_symbols` replays
them onto its own bus before emit.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from a816.linker import Linker
from a816.object_file import ObjectFile
from a816.program import Program


def _write(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


def _compile(src: str, obj_path: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        asm = Path(tmp) / "main.s"
        _write(asm, src)
        program = Program()
        assert program.assemble_as_object(str(asm), obj_path) == 0


def test_map_directive_serialized_into_object() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        obj_path = Path(tmp) / "out.o"
        src = ".map identifier=0x42 bank_range=0x40, 0x4f addr_range=0x0000, 0xffff mask=0x10000\n"
        _compile(src, obj_path)
        loaded = ObjectFile.from_file(str(obj_path))
        assert len(loaded.bus_mappings) == 1
        mapping = loaded.bus_mappings[0]
        assert mapping.identifier == "66"
        assert mapping.bank_range == (0x40, 0x4F)
        assert mapping.addr_range == (0x0000, 0xFFFF)
        assert mapping.mask == 0x10000


def test_linker_dedupes_identical_map_across_modules() -> None:
    src = ".map identifier=0x42 bank_range=0x40, 0x4f addr_range=0x0000, 0xffff mask=0x10000\nfoo:\n    rts\n"
    with tempfile.TemporaryDirectory() as tmp:
        a = Path(tmp) / "a.o"
        b = Path(tmp) / "b.o"
        _compile(src, a)
        _compile(src.replace("foo", "bar"), b)
        linked = Linker([ObjectFile.from_file(str(a)), ObjectFile.from_file(str(b))]).link(base_address=0x8000)
        assert len(linked.bus_mappings) == 1


def test_linker_errors_on_conflicting_map_identifier() -> None:
    src_a = ".map identifier=0x42 bank_range=0x40, 0x4f addr_range=0x0000, 0xffff mask=0x10000\nfoo:\n    rts\n"
    src_b = ".map identifier=0x42 bank_range=0x50, 0x5f addr_range=0x0000, 0xffff mask=0x10000\nbar:\n    rts\n"
    with tempfile.TemporaryDirectory() as tmp:
        a = Path(tmp) / "a.o"
        b = Path(tmp) / "b.o"
        _compile(src_a, a)
        _compile(src_b, b)
        with pytest.raises(ValueError, match="conflicting `.map '66'`"):
            Linker([ObjectFile.from_file(str(a)), ObjectFile.from_file(str(b))]).link(base_address=0x8000)


def test_linker_replays_map_onto_link_program_bus() -> None:
    src = ".map identifier=0x42 bank_range=0x40, 0x4f addr_range=0x0000, 0xffff mask=0x10000\nfoo:\n    rts\n"
    with tempfile.TemporaryDirectory() as tmp:
        obj_path = Path(tmp) / "out.o"
        _compile(src, obj_path)
        linked = Linker([ObjectFile.from_file(str(obj_path))]).link(base_address=0x8000)

        program = Program()
        # Fresh program, fresh bus — only default mappings.
        assert "66" not in program.resolver.bus.mappings
        program.import_linked_symbols(linked)
        # After import, the linked module's `.map` is now on the program's bus.
        assert "66" in program.resolver.bus.mappings
        custom = program.resolver.bus.mappings["66"]
        assert custom.bank_range == (0x40, 0x4F)
