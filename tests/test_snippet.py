"""`assemble_source`: a source string to bytes through compile and link."""

from __future__ import annotations

import pytest

from a816.exceptions import AssemblyError
from a816.snippet import assemble_source

_STUB = "*= 0x7E0000\nclc\nxce\nrep #0x30\nldx #0xCAFE\nbrk #0x42\n"


def test_a_stub_assembles_where_it_places_itself() -> None:
    assert assemble_source(_STUB).code == b"\x18\xfb\xc2\x30\xa2\xfe\xca\x00\x42"


def test_labels_carry_their_linked_address() -> None:
    assembled = assemble_source("*=0x008000\nnop\nhere:\nrts\n")
    assert assembled.symbols["here"] == 0x008001


def test_blocks_carry_their_snes_address() -> None:
    assert assemble_source("*=0x038000\n.db 1\n").blocks == ((0x038000, b"\x01"),)


def test_blocks_come_in_address_order() -> None:
    blocks = assemble_source("*=0x039000\n.db 2\n*=0x038000\n.db 1\n").blocks
    assert [address for address, _ in blocks] == [0x038000, 0x039000]


def test_overlapping_blocks_are_refused() -> None:
    with pytest.raises(AssemblyError, match="E0408"):
        assemble_source("*=0x038000\n.db 1, 2\n*=0x038001\n.db 3\n")


def test_unplaced_code_is_refused() -> None:
    with pytest.raises(AssemblyError, match="E0310"):
        assemble_source("lda #0x42\n")


def test_symbols_are_bound_before_the_source_compiles() -> None:
    assembled = assemble_source(
        "*=0x7E0000\njsr.l kerning_func\nlda.b #pair\n", symbols={"kerning_func": 0x208000, "pair": 7}
    )
    assert assembled.code == b"\x22\x00\x80\x20\xa9\x07"


def test_files_serve_the_source_s_includes() -> None:
    assembled = assemble_source('.include "consts.i"\n*=0x008000\nlda #VALUE\n', files={"consts.i": "VALUE = 0x12\n"})
    assert assembled.code == b"\xa9\x12"


def test_experimental_features_reach_the_build() -> None:
    assembled = assemble_source("*=0x008000\nrep #0x20\nlda #0x42\n", experimental=["track_register_size"])
    assert assembled.code == b"\xc2\x20\xa9\x42\x00"


def test_a_failed_build_raises_with_its_diagnostics() -> None:
    with pytest.raises(AssemblyError, match="E0200"):
        assemble_source("*=0x008000\nlda.w missing\n")


def test_diagnostics_keep_the_source_s_line_numbers() -> None:
    with pytest.raises(AssemblyError, match=r"main\.s:3"):
        assemble_source("*=0x008000\nnop\nlda.w missing\n")


def test_a_wram_stub_keeps_its_address() -> None:
    assert assemble_source(_STUB).blocks[0][0] == 0x7E0000
