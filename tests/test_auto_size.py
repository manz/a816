"""Auto-size opcode emission from typed-instance field accesses + A/I-width warnings."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pytest

from a816.program import Program


def _assemble_ips(src: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        asm = root / "main.s"
        asm.write_text(src, encoding="utf-8")
        ips = root / "out.ips"
        program = Program()
        assert program.assemble_as_patch(str(asm), ips) == 0
        return ips.read_bytes()


_OAM_DEF = """
.struct OAM {
    word x
    word y
    byte tile
    byte attr
}
"""


def test_typed_field_in_long_bank_picks_lda_l() -> None:
    """`p := (0x7e0000 as OAM)` → `lda p.tile` emits `lda.l` (AF op)."""
    src = (
        _OAM_DEF
        + """
        .a8
        *=0x008000
        p := (0x7e0000 as OAM)
            lda p.tile
        """
    )
    data = _assemble_ips(src)
    # Tile offset = 4, base = 0x7E0000. LDA.l $7E0004 → AF 04 00 7E
    assert b"\xaf\x04\x00\x7e" in data


def test_typed_field_in_abs_bank_picks_lda_w() -> None:
    """Base under 0x10000 (abs) → 16-bit absolute addressing."""
    src = (
        _OAM_DEF
        + """
        .a8
        *=0x008000
        p := (0x002100 as OAM)
            lda p.tile
        """
    )
    data = _assemble_ips(src)
    # Tile offset = 4, base = 0x002100. LDA $2104 → AD 04 21
    assert b"\xad\x04\x21" in data


def test_typed_field_in_direct_page_picks_lda_b() -> None:
    """Base under 0x100 (DP) → direct page addressing."""
    src = (
        _OAM_DEF
        + """
        .a8
        *=0x008000
        p := (0x10 as OAM)
            lda p.tile
        """
    )
    data = _assemble_ips(src)
    # Tile offset = 4, base = 0x10. LDA $14 → A5 14
    assert b"\xa5\x14" in data


def test_explicit_size_still_wins() -> None:
    """User-specified `.b/.w/.l` overrides the typed-instance inference."""
    src = (
        _OAM_DEF
        + """
        .a8
        *=0x008000
        p := (0x7e0000 as OAM)
            lda.w 0x0004 + p.tile - 0x7e0000
        """
    )
    # Compose so the expression hits the string heuristic, not the
    # typed-field shortcut. `.w` forces abs.
    data = _assemble_ips(src)
    # Operand evaluates to 4 + 4 = 8. LDA $0008 → AD 08 00
    assert b"\xad\x08\x00" in data


def test_register_width_mismatch_warns_when_a8_loads_word(caplog: pytest.LogCaptureFixture) -> None:
    src = (
        _OAM_DEF
        + """
        .a8
        *=0x008000
        p := (0x7e0000 as OAM)
            lda p.x
        """
    )
    with caplog.at_level(logging.WARNING):
        _assemble_ips(src)
    assert any("field width" in r.message and "A register" in r.message for r in caplog.records), (
        f"expected an A-width warning, got {[r.message for r in caplog.records]}"
    )


def test_no_warning_when_field_matches_register(caplog: pytest.LogCaptureFixture) -> None:
    src = (
        _OAM_DEF
        + """
        .a8
        *=0x008000
        p := (0x7e0000 as OAM)
            lda p.tile
        """
    )
    with caplog.at_level(logging.WARNING):
        _assemble_ips(src)
    assert not any("field width" in r.message for r in caplog.records)


def test_non_typed_operand_keeps_string_heuristic() -> None:
    """Bare numbers / labels are untouched by the typed-field path."""
    src = """
        *=0x008000
            lda 0x12
        """
    data = _assemble_ips(src)
    # `0x12` < 0x100 → DP heuristic → LDA $12 → A5 12
    assert b"\xa5\x12" in data
