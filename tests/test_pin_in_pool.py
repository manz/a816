"""`.alloc NAME at ADDR in POOL { body }`: an alloc pinned inside a pool.

The pool carves the pinned span before placing its floating allocs, so they
pack around it instead of landing on top of it (a plain `.alloc at` next to a
pool is invisible to the allocator; only the write audit would catch the
collision). With `cross_bank` the pinned span may run over several banks:
dq6 pins its dialogue stream at the start of a multi-bank gap.
`.reserve NAME as TYPE at ADDR in POOL` pins a typed bss reservation the same
way.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pytest

from a816.formatter import A816Formatter
from a816.linker import Linker
from a816.object_file import ObjectFile
from a816.pool import PoolOverlapError
from a816.program import Program

_HIROM = ".map identifier=1 bank_range=0x40, 0x7d addr_range=0x0000, 0xffff mask=0x10000\n"
_WRAM = ".map identifier=3 bank_range=0x7e, 0x7f addr_range=0x0000, 0xffff mask=0x10000 writable=1\n"


def _symbols(src: str) -> dict[str, int]:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "blob.bin").write_bytes(bytes(range(256)) * 0x300)  # 0x30000 bytes
        (tmp / "m.s").write_text(src.replace("BLOB", str(tmp / "blob.bin")))
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


def test_a_pinned_alloc_is_carved_before_the_floaters() -> None:
    syms = _symbols(
        _HIROM
        + ".pool g { range 0x500000 0x50ffff  strategy order }\n"
        + ".alloc floater in g {\n    .db 1\n}\n.alloc pinned at 0x500000 in g {\n    .db 2, 3\n}\n"
    )
    assert syms["pinned"] == 0x500000
    assert syms["floater"] == 0x500002


def test_two_pins_that_overlap_are_rejected() -> None:
    src = (
        _HIROM
        + ".pool g { range 0x500000 0x50ffff }\n"
        + ".alloc a at 0x500000 in g {\n    .db 1, 2\n}\n.alloc b at 0x500001 in g {\n    .db 3\n}\n"
    )
    with pytest.raises(PoolOverlapError):
        _symbols(src)


def test_a_pinned_cross_bank_blob_spans_banks_and_floaters_pack_behind_it() -> None:
    syms = _symbols(
        _HIROM
        + ".pool g { range 0x500000 0x53ffff  strategy order }\n"
        + '.alloc stream at 0x500000 in g cross_bank {\n    .incbin "BLOB"\n}\n'
        + ".alloc after in g {\n    .db 1\n}\n"
    )
    assert syms["stream"] == 0x500000
    assert syms["after"] == 0x530000  # 0x30000 bytes later: three banks used


def test_a_pinned_blob_too_big_for_one_bank_needs_cross_bank() -> None:
    src = _HIROM + '.pool g { range 0x500000 0x53ffff }\n.alloc stream at 0x500000 in g {\n    .incbin "BLOB"\n}\n'
    with pytest.raises(Exception, match="overlaps another allocation|outside the ranges"):
        _symbols(src)


def test_a_pin_off_its_alignment_is_rejected() -> None:
    src = _HIROM + ".pool g { range 0x500000 0x50ffff }\n.alloc a at 0x500001 in g align 0x10 {\n    .db 1\n}\n"
    assert not _compiles(src)


def test_size_and_in_pool_do_not_combine() -> None:
    src = _HIROM + ".pool g { range 0x500000 0x50ffff }\n.alloc a at 0x500000 size 4 in g {\n    .db 1\n}\n"
    assert not _compiles(src)


def test_formatter_keeps_the_pool_and_flags_of_a_pin() -> None:
    src = ".alloc stream at 0x500000 in g cross_bank align 0x100 {\n    .db 1\n}\n"
    assert ".alloc stream at 0x500000 in g cross_bank align 0x100 {\n" in A816Formatter().format_text(src)


_TYPED = _WRAM + ".pool wram { bss  range 0x7e0000 0x7e1fff }\n.struct Hdr {\n    word a\n    byte b\n}\n"


def test_a_typed_reserve_can_be_pinned() -> None:
    syms = _symbols(_TYPED + ".reserve hdr as Hdr at 0x7e0100 in wram\n")
    assert (syms["hdr"], syms["hdr.a"], syms["hdr.b"]) == (0x7E0100, 0x7E0100, 0x7E0102)


def test_a_pinned_typed_reserve_overlapping_another_pin_is_rejected() -> None:
    src = _TYPED + ".reserve hdr as Hdr at 0x7e0100 in wram\n.reserve tail 2 at 0x7e0102 in wram\n"
    with pytest.raises(PoolOverlapError):
        _symbols(src)


def test_formatter_keeps_a_pinned_typed_reserve() -> None:
    src = ".reserve hdr as Hdr at 0x7e0100 in wram\n"
    assert src in A816Formatter().format_text(src)
