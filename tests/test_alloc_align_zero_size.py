"""`.alloc NAME in POOL align N` and zero-size allocs.

`align N` places a block on a multiple of N (logical address: what DMA and
the reader's base arithmetic see); the gap before the boundary stays free.
An empty body (an unused slot's empty `.incbin`) binds its label and takes no
space; it used to take one hidden byte.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from a816.formatter import A816Formatter
from a816.linker import Linker
from a816.object_file import ObjectFile
from a816.program import Program

_HIROM = ".map identifier=1 bank_range=0x40, 0x7d addr_range=0x0000, 0xffff mask=0x10000\n"


def _symbols(src: str) -> dict[str, int]:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "empty.bin").write_bytes(b"")
        (tmp / "m.s").write_text(src.replace("EMPTY", str(tmp / "empty.bin")))
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


_ORDERED = _HIROM + ".pool g { range 0x500010 0x50ffff  strategy order }\n"


def test_align_places_on_the_boundary_and_keeps_the_gap_free() -> None:
    syms = _symbols(_ORDERED + ".alloc aligned in g align 0x100 {\n    .db 1\n}\n.alloc small in g {\n    .db 2\n}\n")
    assert syms["aligned"] == 0x500100
    assert syms["small"] == 0x500010  # the gap before the boundary is still free


def test_an_already_aligned_start_is_used_as_is() -> None:
    syms = _symbols(_HIROM + ".pool g { range 0x500000 0x50ffff }\n.alloc a in g align 0x10000 {\n    .db 1\n}\n")
    assert syms["a"] == 0x500000


def test_align_accepts_constant_expressions() -> None:
    syms = _symbols(_ORDERED + "BOUNDARY = 0x80\n.alloc a in g align BOUNDARY * 2 {\n    .db 1\n}\n")
    assert syms["a"] == 0x500100


def test_align_must_be_a_power_of_two() -> None:
    assert not _compiles(_HIROM + ".pool g { range 0x500000 0x50ffff }\n.alloc a in g align 3 {\n    .db 1\n}\n")


def test_formatter_keeps_align() -> None:
    src = ".alloc a in g align 0x100 {\n    .db 1\n}\n"
    assert ".alloc a in g align 0x100 {\n" in A816Formatter().format_text(src)


def test_an_empty_alloc_takes_no_space() -> None:
    syms = _symbols(_ORDERED + ".alloc empty in g {\n}\n.alloc next in g {\n    .db 1\n}\n")
    assert syms["empty"] == syms["next"] == 0x500010


def test_an_empty_incbin_takes_no_space() -> None:
    syms = _symbols(_ORDERED + '.alloc slot in g {\n    .incbin "EMPTY"\n}\n.alloc next in g {\n    .db 1\n}\n')
    assert syms["slot"] == syms["next"] == 0x500010
