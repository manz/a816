"""`.b` immediates that cannot fit one byte are an error; `.w`/`.l` keep masking.

`lda.b #0x1234` used to emit `a9 34` silently. The user asked for a byte
and the value does not fit, so the assembler refuses. Wider immediates keep
truncating on purpose: `lda.w #symbol` loading the low word of a 24-bit
address is a standard SNES idiom.

Negative values: a byte immediate accepts -0x100..0xFF, i.e. the bits above
bit 7 must be all zero or all one. That keeps `lda.b #-1` (0xFF) and
`and.b #~0x80` (0x7F) working.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a816.error_codes import E_CODEGEN_IMMEDIATE_OVERFLOW
from a816.parse.nodes import NodeError
from a816.program import Program
from tests import StubWriter


def _emit(src: str) -> bytes:
    program = Program()
    writer = StubWriter()
    program.assemble_string_with_emitter("*=0x008000\n" + src, "ovf.s", writer)
    return b"".join(writer.data)


class TestByteOverflowErrors:
    @pytest.mark.parametrize(
        "src",
        [
            "lda.b #0x1234\n",
            "ldx.b #0x100\n",
            "lda.b #-0x101\n",
            "lda.b #FWD\nFWD = 0xDEAD\n",
            "lda.b #target\ntarget:\n",
        ],
    )
    def test_rejected(self, src: str) -> None:
        with pytest.raises(NodeError) as exc:
            _emit(src)
        assert exc.value.code == str(E_CODEGEN_IMMEDIATE_OVERFLOW)

    def test_error_is_located(self) -> None:
        with pytest.raises(NodeError) as exc:
            _emit("nop\nlda.b #0x1234\n")
        rendered = exc.value.format()
        assert "ovf.s:3" in rendered
        assert "0x1234" in rendered


class TestAccepted:
    @pytest.mark.parametrize(
        ("src", "expected"),
        [
            ("lda.b #0xFF\n", b"\xa9\xff"),
            ("lda.b #-1\n", b"\xa9\xff"),
            ("and.b #~0x80\n", b"\x29\x7f"),
            ("lda.b #-0x100\n", b"\xa9\x00"),
            ("lda.w #0x018034\n", b"\xa9\x34\x80"),
            (".a16\nlda #0x018034\n", b"\xa9\x34\x80"),
            ("lda.b 0x1234\n", b"\xa5\x34"),  # direct page: address masking stays
            (".a8\nlda #0x1234\n", b"\xa9\x34\x12"),  # value-driven .w: warns, no error
        ],
    )
    def test_emits(self, src: str, expected: bytes) -> None:
        assert _emit(src) == expected

    def test_external_symbol_defers_to_linker(self, tmp_path: Path) -> None:
        src = tmp_path / "ext.s"
        src.write_text(".extern far_value\n*=0x008000\nlda.b #far_value\n", encoding="utf-8")
        assert Program().assemble_as_object(str(src), tmp_path / "ext.o") == 0
