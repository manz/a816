"""`rep` / `sep` with constant immediate should auto-update assembler-time
register sizes so source no longer needs `.a8` / `.a16` / `.i8` / `.i16`
after every register-size change."""

from __future__ import annotations

import pytest

from a816.parse.nodes import NodeError
from a816.program import Program
from tests import StubWriter


def _assemble(src: str) -> StubWriter:
    program = Program()
    # rep/sep -> a_size/i_size inference is opt-in (experimental); the
    # whole suite exercises that behavior so flip the resolver flag
    # before assembling.
    program.resolver.track_register_size = True
    writer = StubWriter()
    program.assemble_string_with_emitter(src, "test_repsep.s", writer)
    return writer


def _emitted_bytes(writer: StubWriter) -> bytes:
    return b"".join(writer.data)


class TestRepSetTracksASize:
    def test_rep_30_widens_small_immediate_to_16bit(self) -> None:
        # `lda #0x42` fits in 8 bits and would normally emit `A9 42`.
        # After `rep #0x30` the inference flips A to 16-bit, so the
        # same operand becomes `A9 42 00` — proves the size choice
        # is driven by M, not by the value's width.
        before = _assemble("*=0x008000\nlda #0x42\n")
        after = _assemble("*=0x008000\nrep #0x30\nlda #0x42\n")
        assert _emitted_bytes(before) == b"\xa9\x42"
        assert _emitted_bytes(after) == b"\xc2\x30\xa9\x42\x00"

    def test_rep_30_lets_lda_imm_emit_16bit_without_explicit_directive(self) -> None:
        src = "*=0x008000\nrep #0x30\nlda #0xbeef\n"
        writer = _assemble(src)
        # rep #$30 = C2 30; lda #imm16 = A9 EF BE — 5 bytes total.
        # (The wide immediate would emit 2-byte either way; this just
        # confirms the byte order under the widened form.)
        assert _emitted_bytes(writer) == b"\xc2\x30\xa9\xef\xbe"

    def test_sep_20_after_rep_30_restores_8bit_immediate(self) -> None:
        src = "*=0x008000\nrep #0x30\nlda #0xbeef\nsep #0x20\nlda #0x42\n"
        writer = _assemble(src)
        # rep #$30 (C2 30) → A=16; lda #imm16 (A9 EF BE);
        # sep #$20 (E2 20) → A=8; lda #imm8 (A9 42).
        assert _emitted_bytes(writer) == b"\xc2\x30\xa9\xef\xbe\xe2\x20\xa9\x42"

    def test_rep_10_only_affects_x_not_a(self) -> None:
        # rep #$10 clears X (index) but leaves M (A) alone.
        # `ldx #imm16` widens; `lda #imm8` stays 8-bit.
        src = "*=0x008000\nrep #0x10\nldx #0x1234\nlda #0x42\n"
        writer = _assemble(src)
        assert _emitted_bytes(writer) == b"\xc2\x10\xa2\x34\x12\xa9\x42"


class TestExplicitDirectiveStillWins:
    def test_a16_overrides_after_sep(self) -> None:
        # User says A=16 with .a16 even though sep #$20 would set it to 8.
        # Explicit directive runs after rep/sep in source, so the
        # directive's later mutation wins for subsequent ops.
        src = "*=0x008000\nsep #0x20\n.a16\nlda #0xbeef\n"
        writer = _assemble(src)
        assert _emitted_bytes(writer) == b"\xe2\x20\xa9\xef\xbe"


class TestForwardReferenceImmediate:
    def test_rep_with_forward_referenced_constant_still_resolves(self) -> None:
        # A constant assignment binds at codegen time, so `FLAGS` is
        # already known when pass 1 reaches the `rep`.
        src = "*=0x008000\nrep #FLAGS\nlda #0xbeef\nFLAGS = 0x30\n"
        writer = _assemble(src)
        assert _emitted_bytes(writer) == b"\xc2\x30\xa9\xef\xbe"

    def test_rep_with_forward_label_operand_is_an_error(self) -> None:
        # Labels bind on the first pass only, so a `rep` that cannot be
        # evaluated there would size the code after it at the old width
        # and every later label would drift off its bytes. Stay loud.
        src = "*=0x008000\nrep.b #flags & 0x30\nlda #0x12\n*=0x008020\nflags:\n"
        with pytest.raises(NodeError, match="E0200"):
            _assemble(src)


class TestSymbolicImmediateStillUpdatesSize:
    def test_rep_with_assemble_time_constant_propagates(self) -> None:
        # `rep #FLAGS` resolves the immediate at assembly-time the same
        # as a literal — equate is just a named constant. Subsequent
        # `lda #` picks 16-bit width.
        src = "FLAGS = 0x30\n*=0x008000\nrep #FLAGS\nlda #0xbeef\n"
        writer = _assemble(src)
        assert _emitted_bytes(writer) == b"\xc2\x30\xa9\xef\xbe"


class TestFlagSizesEndWithTheFlow:
    """A tracked `rep`/`sep` is the routine's runtime state: the code after an
    `rts`/`jmp`/`bra`/`plp` is reached from elsewhere, back at 8 bits."""

    def test_rts_ends_a_rep_widened_accumulator(self) -> None:
        """ff4 `libmz`: `_enable_display`'s `lda #0` emitted 16-bit after another routine's `rep`."""
        writer = _assemble("*=0x008000\nrep #0x20\nlda #0x1234\nrts\nlda #0x00\nrts\n")

        assert _emitted_bytes(writer) == b"\xc2\x20\xa9\x34\x12\x60\xa9\x00\x60"

    def test_jmp_ends_a_rep_widened_index(self) -> None:
        writer = _assemble("*=0x008000\nrep #0x10\njmp.w 0x8000\nldx #0x01\n")

        assert _emitted_bytes(writer) == b"\xc2\x10\x4c\x00\x80\xa2\x01"

    def test_a_declared_size_outlives_rts(self) -> None:
        """`.a16` over a file of 16-bit routines must hold across their `rts`."""
        writer = _assemble("*=0x008000\n.a16\nlda #0x01\nrts\nlda #0x02\n")

        assert _emitted_bytes(writer) == b"\xa9\x01\x00\x60\xa9\x02\x00"

    def test_a_label_after_the_boundary_binds_where_its_bytes_land(self) -> None:
        """Label passes reset at the same boundary as emit, or labels shift."""
        writer = _assemble("*=0x008000\nrep #0x20\nrts\nlda #0x00\ntarget:\nbra target\n")

        assert _emitted_bytes(writer) == b"\xc2\x20\x60\xa9\x00\x80\xfe"
