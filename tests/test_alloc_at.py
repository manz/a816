"""Behavioural tests for `.alloc … at ADDR [size N] { ... }`.

PR1 adds the pinned placement form. Covers:

- Anonymous pinned alloc places bytes at the literal address.
- Named pinned alloc binds the NAME label at the address.
- Body overflow vs `size N` is a hard error.
- Two pinned allocs at the same source line collide on pool decl
  (rather than silently last-write-wins).
- Pinned + pooled forms coexist in the same module.
"""

from __future__ import annotations

import io

import pytest

from a816.exceptions import AssemblyError
from a816.program import Program
from a816.writers import OverlapError, SFCWriter


def _assemble(src: str) -> tuple[Program, bytes]:
    p = Program(overlap_mode="error")
    buf = io.BytesIO()
    w = SFCWriter(buf)
    p.assemble_string_with_emitter(src, "t.s", w)
    return p, buf.getvalue()


class TestPinnedAtSyntax:
    def test_anonymous_at_places_bytes(self) -> None:
        _, data = _assemble("""
        .alloc at 0x008000 {
            .db 0x42
        }
        """)
        assert data[0:1] == b"\x42"

    def test_named_at_binds_symbol(self) -> None:
        program, _ = _assemble("""
        .alloc reset at 0x008000 {
            .db 0
        }
        """)
        labels = program.resolver.current_scope.labels
        assert "reset" in labels
        assert labels["reset"] == 0x008000

    def test_size_clause_bounds_body(self) -> None:
        # 2-byte body inside a 4-byte slot is fine.
        _, data = _assemble("""
        .alloc at 0x008000 size 0x04 {
            .db 0xAA, 0xBB
        }
        """)
        assert data[0:2] == b"\xaa\xbb"

    def test_size_overflow_is_hard_error(self) -> None:
        # 3-byte body inside a 2-byte slot must fail with a clear PoolError.
        with pytest.raises(Exception, match="does not fit"):
            _assemble("""
            .alloc at 0x008000 size 0x02 {
                .db 0x01, 0x02, 0x03
            }
            """)

    def test_two_anon_at_same_address_collide_via_overlap_error(self) -> None:
        # Two anon `.alloc at` blocks targeting the same byte get
        # distinct synthetic pool names (uniqued by source line) BUT
        # both write to the same address, so the WriteAuditor's
        # overlap-hard-error catches the collision.
        with pytest.raises(OverlapError):
            _assemble(
                ".alloc at 0x008000 { .db 1 }\n.alloc at 0x008000 { .db 2 }",
            )

    def test_pinned_and_pooled_coexist(self) -> None:
        program, data = _assemble("""
        .pool client {
            range 0x008100 0x00FFBF
            strategy order
        }

        .alloc reset at 0x008000 {
            .db 0xEA
        }

        .alloc routine in client {
            .db 0x60
        }
        """)
        labels = program.resolver.current_scope.labels
        assert labels["reset"] == 0x008000
        # Pooled alloc lands inside the client pool's range.
        assert 0x008100 <= labels["routine"] <= 0x00FFBF
        assert data[0:1] == b"\xea"


class TestPinnedAtErrors:
    def test_at_without_address_raises_parse_error(self) -> None:
        with pytest.raises(AssemblyError):
            _assemble(".alloc at { .db 1 }")

    def test_bad_separator_after_name_raises(self) -> None:
        with pytest.raises(AssemblyError):
            _assemble(".alloc foo via 0x8000 { .db 1 }")


class TestOverlapHardError:
    def test_overlapping_pinned_allocs_fail_build(self) -> None:
        # Two pinned allocs whose ranges overlap should trip OverlapError.
        # Auditor reports the physical file offset (LoROM: $00:8000 = file
        # offset $000000), which is what the diagnostic surfaces today.
        with pytest.raises(OverlapError, match=r"\$000002"):
            _assemble("""
            .alloc a at 0x008000 size 0x04 {
                .db 1, 2, 3, 4
            }
            .alloc b at 0x008002 size 0x04 {
                .db 0xA, 0xB, 0xC, 0xD
            }
            """)
