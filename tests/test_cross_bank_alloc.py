"""`.alloc NAME in POOL cross_bank { data }`: a data blob may straddle bank edges
where the ROM is physically contiguous.

Large position-independent blobs (text banks, fonts) are read as `base + offset`
by code that steps the bank edge the mapper's way (LoROM: low word back to
$8000, bank + 1). Pools keep every other block bank-local; a flagged block may
span adjacent free chunks when the last byte of one and the first byte of the
next are consecutive in the ROM. The body is data only and label-free: nothing
inside it needs an address, the reader works from the base.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from a816.exceptions import PoolOverflowLinkError
from a816.formatter import A816Formatter
from a816.linker import Linker
from a816.module_builder import build_with_imports
from a816.object_file import ObjectFile
from a816.parse.ast.nodes import AllocAstNode
from a816.parse.mzparser import A816Parser
from a816.program import Program

_LOROM = ".map identifier=1 bank_range=0x00, 0x3f addr_range=0x8000, 0xffff mask=0x8000\n"
_HIROM = ".map identifier=1 bank_range=0xc0, 0xff addr_range=0x0000, 0xffff mask=0x10000\n"
_BLOB_SIZE = 0x1800


def _link(src: str) -> ObjectFile:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "blob.bin").write_bytes(bytes(i & 0xFF for i in range(_BLOB_SIZE)))
        (tmp / "m.s").write_text(src.replace("blob.bin", str(tmp / "blob.bin")))
        assert Program().assemble_as_object(str(tmp / "m.s"), tmp / "m.o") == 0
        return Linker([ObjectFile.from_file(str(tmp / "m.o"))]).link(base_address=0x8000)


def _assembles(src: str) -> bool:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "blob.bin").write_bytes(bytes(_BLOB_SIZE))
        (tmp / "m.s").write_text(src.replace("blob.bin", str(tmp / "blob.bin")))
        return Program().assemble_as_object(str(tmp / "m.s"), tmp / "m.o") == 0


def _blob_section_base(linked: ObjectFile) -> int:
    (section,) = [s for s in linked.sections if len(s.code) == _BLOB_SIZE]
    return section.placed_base


_BLOB = '.alloc text in blobs cross_bank {\n    .incbin "blob.bin"\n}\n'


def test_lorom_blob_spans_the_edge_into_the_next_rom_window() -> None:
    linked = _link(
        _LOROM + ".pool blobs { range 0x00f000 0x00ffff  range 0x018000 0x018fff  strategy order }\n" + _BLOB
    )
    assert _blob_section_base(linked) == 0x00F000


def test_hirom_blob_spans_the_edge_into_the_next_bank() -> None:
    linked = _link(
        _HIROM + ".pool blobs { range 0xc0f000 0xc0ffff  range 0xc10000 0xc10fff  strategy order }\n" + _BLOB
    )
    assert _blob_section_base(linked) == 0xC0F000


def test_a_gap_in_the_rom_is_not_crossed() -> None:
    """Bank $01 is skipped: $00:FFFF and $02:8000 aren't consecutive ROM bytes."""
    src = _LOROM + ".pool blobs { range 0x00f000 0x00ffff  range 0x028000 0x028fff  strategy order }\n" + _BLOB
    with pytest.raises(PoolOverflowLinkError):
        _link(src)


def test_without_the_flag_the_blob_stays_bank_local() -> None:
    src = (
        _LOROM
        + ".pool blobs { range 0x00f000 0x00ffff  range 0x018000 0x018fff  strategy order }\n"
        + _BLOB.replace(" cross_bank", "")
    )
    with pytest.raises(PoolOverflowLinkError):
        _link(src)


def test_code_in_a_cross_bank_body_is_rejected(caplog: pytest.LogCaptureFixture) -> None:
    src = _LOROM + ".pool blobs { range 0x00f000 0x00ffff }\n.alloc f in blobs cross_bank {\n    nop\n}\n"
    assert not _assembles(src)
    assert "E0336" in caplog.text


def test_labels_in_a_cross_bank_body_are_rejected(caplog: pytest.LogCaptureFixture) -> None:
    src = _LOROM + ".pool blobs { range 0x00f000 0x00ffff }\n.alloc t in blobs cross_bank {\ninner:\n    .db 1\n}\n"
    assert not _assembles(src)
    assert "E0336" in caplog.text


def test_canonical_form_keeps_cross_bank() -> None:
    (alloc,) = [n for n in A816Parser.parse_as_ast(_BLOB, filename="t.s").nodes if isinstance(n, AllocAstNode)]
    assert alloc.to_canonical().startswith(".alloc text in blobs cross_bank {")


def test_overflow_hint_points_at_cross_bank() -> None:
    src = (
        _LOROM
        + ".pool blobs { range 0x00f000 0x00ffff  range 0x018000 0x018fff  strategy order }\n"
        + _BLOB.replace(" cross_bank", "")
    )
    with pytest.raises(PoolOverflowLinkError) as excinfo:
        _link(src)
    assert "cross_bank" in excinfo.value.overflow.hint


def test_formatter_keeps_cross_bank() -> None:
    assert ".alloc text in blobs cross_bank {\n" in A816Formatter().format_text(_BLOB)


def test_lorom_blob_bytes_are_contiguous_in_the_rom_file() -> None:
    """The writer puts a section at its base's file offset and writes it
    straight on: across a LoROM edge that is exactly where the next window's
    bytes live ($00:F000 is file 0x7000; $01:8000 follows at 0x8000)."""
    payload = bytes(i & 0xFF for i in range(_BLOB_SIZE))
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "blob.bin").write_bytes(payload)
        src = _LOROM + ".pool blobs { range 0x00f000 0x00ffff  range 0x018000 0x018fff  strategy order }\n" + _BLOB
        (tmp / "m.s").write_text(src.replace("blob.bin", str(tmp / "blob.bin")))
        result = build_with_imports(tmp / "m.s", tmp / "out.sfc", output_format="sfc", output_dir=tmp / "obj")
        assert result.exit_code == 0
        rom = (tmp / "out.sfc").read_bytes()
    assert rom[0x7000 : 0x7000 + _BLOB_SIZE] == payload


def test_an_aligned_cross_bank_blob_starts_on_its_boundary_and_spans() -> None:
    linked = _link(
        _HIROM
        + ".pool blobs { range 0xc0f010 0xc0ffff  range 0xc10000 0xc10fff  strategy order }\n"
        + '.alloc text in blobs cross_bank align 0x100 {\n    .incbin "blob.bin"\n}\n'
    )
    assert _blob_section_base(linked) == 0xC0F100
