"""`rep` / `sep` with constant immediate should auto-update assembler-time
register sizes so source no longer needs `.a8` / `.a16` / `.i8` / `.i16`
after every register-size change."""

from __future__ import annotations

from a816.program import Program
from tests import StubWriter


def _assemble(src: str) -> StubWriter:
    program = Program()
    writer = StubWriter()
    program.assemble_string_with_emitter(src, "test_repsep.s", writer)
    return writer


def _emitted_bytes(writer: StubWriter) -> bytes:
    return b"".join(writer.data)


class TestRepSetTracksASize:
    def test_rep_30_lets_lda_imm_emit_16bit_without_explicit_directive(self) -> None:
        src = "*=0x008000\nrep #0x30\nlda #0xbeef\n"
        writer = _assemble(src)
        # rep #$30 = C2 30; lda #imm16 = A9 EF BE — 5 bytes total.
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
        # Pass 1 hits `rep #FLAGS` before `FLAGS = 0x30` is bound.
        # SymbolNotDefined is swallowed; pass 2 picks up the value
        # and widens `lda #imm`.
        src = "*=0x008000\nrep #FLAGS\nlda #0xbeef\nFLAGS = 0x30\n"
        writer = _assemble(src)
        assert _emitted_bytes(writer) == b"\xc2\x30\xa9\xef\xbe"


class TestSymbolicImmediateStillUpdatesSize:
    def test_rep_with_assemble_time_constant_propagates(self) -> None:
        # `rep #FLAGS` resolves the immediate at assembly-time the same
        # as a literal — equate is just a named constant. Subsequent
        # `lda #` picks 16-bit width.
        src = "FLAGS = 0x30\n*=0x008000\nrep #FLAGS\nlda #0xbeef\n"
        writer = _assemble(src)
        assert _emitted_bytes(writer) == b"\xc2\x30\xa9\xef\xbe"
