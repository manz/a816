"""`.pad_to OFFSET [, BYTE]`: fill the current block with BYTE until it is
OFFSET bytes long.

The block is the enclosing `.alloc` body or `*=` section. Its position is
known on the first pass, so the padding needs no `sizeof` of what came
before (a `.for` over `N - sizeof(blob)` expands too early to work).
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pytest

from a816.formatter import A816Formatter
from a816.linker import Linker
from a816.object_file import ObjectFile
from a816.parse.mzparser import A816Parser
from a816.program import Program
from tests import link_source

_PREAMBLE = """
.map identifier=1 bank_range=0xc0, 0xfd addr_range=0x0000, 0xffff mask=0x10000 mirror_bank_range=0x40, 0x7d
.map identifier=3 bank_range=0x7e, 0x7f addr_range=0x0000, 0xffff mask=0x10000 writable=1
.pool code { range 0xc10000 0xc1ffff  strategy order }
"""


def _built(src: str) -> bytes:
    linked = link_source(_PREAMBLE + src)
    assert linked.exit_code == 0
    return b"".join(linked.data)


def _error(src: str, caplog: pytest.LogCaptureFixture) -> str:
    """The diagnostics of a build that fails on `src`."""
    with caplog.at_level(logging.ERROR, logger="a816"):
        linked = link_source(_PREAMBLE + src)
    assert linked.exit_code != 0
    return caplog.text


def _linked_code(src: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmpdir:
        asm = Path(tmpdir) / "m.s"
        asm.write_text(_PREAMBLE + src, encoding="utf-8")
        obj = Path(tmpdir) / "m.o"
        assert Program().assemble_as_object(str(asm), obj) == 0
        linked = Linker([ObjectFile.from_file(str(obj))]).link(base_address=0x8000)
    return b"".join(section.code for section in linked.sections)


def _alloc(body: str) -> str:
    return ".alloc table in code {\n" + body + "}\n"


def test_pads_an_alloc_body_with_zeroes() -> None:
    assert _built(_alloc(".db 1, 2\n.pad_to 8\n")) == b"\x01\x02" + bytes(6)


def test_pads_with_the_given_byte() -> None:
    assert _built(_alloc(".db 1\n.pad_to 4, 0xff\n")) == b"\x01\xff\xff\xff"


def test_a_label_after_the_padding_binds_at_the_offset() -> None:
    src = _alloc(".db 1\n.pad_to 4\ntail:\n.dw tail & 0xffff\n")

    assert _built(src)[4:] == b"\x04\x00"


def test_sizes_the_alloc_to_the_offset() -> None:
    src = _alloc(".db 1\n.pad_to 0x10\n") + ".alloc after in code {\n.dw after & 0xffff\n}\n"

    assert _built(src)[0x10:] == b"\x10\x00"


def test_pads_twice_in_one_block() -> None:
    assert _built(_alloc(".db 1\n.pad_to 2\n.db 3\n.pad_to 4\n")) == b"\x01\x00\x03\x00"


def test_counts_bytes_inside_a_nested_scope() -> None:
    assert _built(_alloc("{\n.db 1, 2\n}\n.pad_to 3\n")) == b"\x01\x02\x00"


def test_counts_bytes_across_a_reloc_address() -> None:
    assert _built(_alloc(".db 1\n@= 0x7e0000\n.db 2\n.pad_to 4\n")) == b"\x01\x02\x00\x00"


def test_an_offset_equal_to_the_length_pads_nothing() -> None:
    assert _built(_alloc(".db 1, 2\n.pad_to 2\n")) == b"\x01\x02"


def test_pads_a_star_eq_section() -> None:
    assert _built("*= 0xc10000\n.db 1\n.pad_to 3\n") == b"\x01\x00\x00"


def test_a_star_eq_starts_a_new_block() -> None:
    assert _built("*= 0xc10000\n.db 1, 2, 3\n*= 0xc10010\n.db 4\n.pad_to 2\n")[3:] == b"\x04\x00"


def test_pads_from_a_macro_body() -> None:
    src = ".macro pad(n) {\n.pad_to n, 0xaa\n}\n" + _alloc(".db 1\npad(4)\n")

    assert _built(src) == b"\x01\xaa\xaa\xaa"


def test_counts_bytes_inside_an_if() -> None:
    assert _built(_alloc(".db 1\n.if 1 {\n.db 2\n}\n.pad_to 4\n")) == b"\x01\x02\x00\x00"


def test_the_offset_may_be_a_constant() -> None:
    assert _built("SIZE = 4\n" + _alloc(".db 1\n.pad_to SIZE\n")) == b"\x01" + bytes(3)


def test_pads_an_alloc_body_in_an_object_build() -> None:
    assert _linked_code(_alloc(".db 1\n.pad_to 4, 0xee\n")) == b"\x01\xee\xee\xee"


def test_pads_a_star_eq_section_in_an_object_build() -> None:
    assert _linked_code("*= 0xc10000\n.db 1\n.pad_to 3\n") == b"\x01\x00\x00"


def test_pads_a_cross_bank_blob() -> None:
    src = ".alloc blob in code cross_bank {\n.db 1\n.pad_to 2\n}\n"

    assert _built(src) == b"\x01\x00"


def test_an_overrun_names_the_block_length(caplog: pytest.LogCaptureFixture) -> None:
    message = "`.pad_to 0x2`: the block is already 0x3 bytes long"
    assert message in _error(_alloc(".db 1, 2, 3\n.pad_to 2\n"), caplog)


def test_an_overrun_is_e0349(caplog: pytest.LogCaptureFixture) -> None:
    assert "error[E0349]" in _error(_alloc(".db 1, 2, 3\n.pad_to 2\n"), caplog)


def test_an_overrun_fails_an_object_build() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        asm = Path(tmpdir) / "m.s"
        asm.write_text(_PREAMBLE + _alloc(".db 1, 2, 3\n.pad_to 2\n"), encoding="utf-8")

        assert Program().assemble_as_object(str(asm), Path(tmpdir) / "m.o") != 0


def test_rejects_a_fill_wider_than_a_byte(caplog: pytest.LogCaptureFixture) -> None:
    assert "fill byte 0x100 is not a byte" in _error(_alloc(".pad_to 2, 0x100\n"), caplog)


def test_rejects_a_negative_offset(caplog: pytest.LogCaptureFixture) -> None:
    assert "offset -1 is negative" in _error(_alloc(".pad_to -1\n"), caplog)


def test_rejects_padding_outside_a_block(caplog: pytest.LogCaptureFixture) -> None:
    assert "outside an `.alloc` body or `*=` section" in _error(".pad_to 2\n", caplog)


def test_formats_the_default_fill() -> None:
    assert A816Formatter().format_text(_alloc(".pad_to 8\n")) == ".alloc table in code {\n    .pad_to 8\n}\n"


def test_formats_an_explicit_fill() -> None:
    src = _alloc(".pad_to 0x10, 0xff\n")

    assert A816Formatter().format_text(src) == ".alloc table in code {\n    .pad_to 0x10, 0xff\n}\n"


def test_rejects_a_string_offset(caplog: pytest.LogCaptureFixture) -> None:
    assert "`.pad_to` takes integers" in _error(_alloc('.pad_to "8"\n'), caplog)


def test_represents_the_default_fill_as_none() -> None:
    (node,) = A816Parser.parse_as_ast(".pad_to 8\n", "m.s").nodes

    assert node.to_representation() == ("pad_to", "8", None)


def test_represents_an_explicit_fill() -> None:
    (node,) = A816Parser.parse_as_ast(".pad_to 8, 0xff\n", "m.s").nodes

    assert node.to_representation() == ("pad_to", "8", "0xff")
