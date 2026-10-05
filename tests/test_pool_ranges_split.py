"""A `range` over several banks splits into one bank-local range per bank,
clipped to the windows the bus serves for the pool's kind (ROM, or writable
memory for `bss`). Blocks stay bank-local, so the split is only shorthand.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from a816.linker import Linker
from a816.object_file import ObjectFile
from a816.program import Program

_HIROM = ".map identifier=1 bank_range=0x40, 0x7d addr_range=0x0000, 0xffff mask=0x10000\n"
_LOROM = ".map identifier=1 bank_range=0x00, 0x3f addr_range=0x8000, 0xffff mask=0x8000\n"


def _symbols(src: str) -> dict[str, int]:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "m.s").write_text(src)
        assert Program().assemble_as_object(str(tmp / "m.s"), tmp / "m.o") == 0
        linked = Linker([ObjectFile.from_file(str(tmp / "m.o"))]).link(base_address=0x8000)
    return {name: value for name, value, *_ in linked.symbols}


def _compiles(src: str) -> bool:
    logging.disable(logging.CRITICAL)
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            (tmp / "m.s").write_text(src)
            return Program().assemble_as_object(str(tmp / "m.s"), tmp / "m.o") == 0
    finally:
        logging.disable(logging.NOTSET)


def test_a_multi_bank_range_splits_per_bank() -> None:
    """A 0x8000-byte block can't use the 0x1000 left in bank $50."""
    syms = _symbols(
        _HIROM
        + ".pool g { range 0x50f000 0x51ffff }\n"
        + ".alloc a in g {\n    .res 0x8000\n}\n.alloc b in g {\n    .res 0x1000\n}\n"
    )
    assert (syms["a"], syms["b"]) == (0x510000, 0x50F000)


def test_a_lorom_multi_bank_range_keeps_to_the_rom_windows() -> None:
    """`$03:0000-7FFF` is not ROM on LoROM (a816's legacy map would alias it
    onto `$03:8000`): only the `$8000-$FFFF` halves become ranges."""
    syms = _symbols(
        _LOROM
        + ".pool g { range 0x028000 0x03ffff }\n"
        + ".alloc a in g {\n    .res 0x8000\n}\n.alloc b in g {\n    .res 0x8000\n}\n"
    )
    assert sorted((syms["a"], syms["b"])) == [0x028000, 0x038000]


def test_a_multi_bank_bss_range_keeps_to_writable_windows() -> None:
    syms = _symbols(
        ".map identifier=3 bank_range=0x7e, 0x7f addr_range=0x0000, 0xffff mask=0x10000 writable=1\n"
        ".pool wram { bss  range 0x7ef000 0x7f0fff }\n"
        ".reserve a 0x1000 in wram\n.reserve b 0x1000 in wram\n"
    )
    assert sorted((syms["a"], syms["b"])) == [0x7EF000, 0x7F0000]


def test_a_range_running_into_an_unmapped_bank_is_rejected() -> None:
    """Bank $7E has no ROM `.map`: dropping that part silently would shrink the pool."""
    assert not _compiles(_HIROM + ".pool g { range 0x7df000 0x7e0fff }\n")


def test_a_single_bank_range_is_unchanged() -> None:
    syms = _symbols(_HIROM + ".pool g { range 0x500010 0x50ffff }\n.alloc a in g {\n    .db 1\n}\n")
    assert syms["a"] == 0x500010
