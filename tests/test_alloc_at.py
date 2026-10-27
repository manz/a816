"""Behavioural tests for `.alloc … at ADDR [size N] { ... }`.

PR1 adds the pinned placement form. Covers:

- Anonymous pinned alloc places bytes at the literal address.
- Named pinned alloc binds the NAME label at the address.
- Body overflow vs `size N` is a hard error.
- Two pinned allocs over the same bytes fail the link
  (rather than silently last-write-wins).
- Pinned + pooled forms coexist in the same module.
"""

from __future__ import annotations

import logging

import pytest

from tests import Linked, link_source


def _assemble(src: str) -> tuple[Linked, bytes]:
    """The linked build of `src` and its ROM image from offset 0 ($00:8000)."""
    linked = link_source(src)
    assert linked.exit_code == 0
    data = bytearray(max((address + len(block) for address, block in linked.blocks), default=0))
    for address, block in linked.blocks:
        data[address : address + len(block)] = block
    return linked, bytes(data)


def _failure(src: str, caplog: pytest.LogCaptureFixture) -> str:
    """The errors a failed build of `src` logs."""
    with caplog.at_level(logging.ERROR, logger="a816"):
        linked = link_source(src)
    assert linked.exit_code != 0
    return caplog.text


class TestPinnedAtSyntax:
    def test_anonymous_at_places_bytes(self) -> None:
        _, data = _assemble("""
        .alloc at 0x008000 {
            .db 0x42
        }
        """)
        assert data[0:1] == b"\x42"

    def test_named_at_binds_symbol(self) -> None:
        linked, _ = _assemble("""
        .alloc reset at 0x008000 {
            .db 0
        }
        """)
        assert linked.symbols["reset"] == 0x008000

    def test_size_clause_bounds_body(self) -> None:
        # 2-byte body inside a 4-byte slot is fine.
        _, data = _assemble("""
        .alloc at 0x008000 size 0x04 {
            .db 0xAA, 0xBB
        }
        """)
        assert data[0:2] == b"\xaa\xbb"

    def test_size_overflow_is_hard_error(self, caplog: pytest.LogCaptureFixture) -> None:
        # 3-byte body inside a 2-byte slot must fail with a clear pool error.
        src = """
        .alloc at 0x008000 size 0x02 {
            .db 0x01, 0x02, 0x03
        }
        """
        assert "does not fit" in _failure(src, caplog)

    def test_two_anon_at_same_address_collide_via_overlap_error(self, caplog: pytest.LogCaptureFixture) -> None:
        # Two anon `.alloc at` blocks targeting the same byte get
        # distinct synthetic pool names (uniqued by source line) BUT
        # both write to the same address, so the linker's overlap
        # check (E0408) catches the collision.
        src = ".alloc at 0x008000 { .db 1 }\n.alloc at 0x008000 { .db 2 }"
        assert "E0408" in _failure(src, caplog)

    def test_pinned_and_pooled_coexist(self) -> None:
        linked, data = _assemble("""
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
        assert linked.symbols["reset"] == 0x008000
        # Pooled alloc lands inside the client pool's range.
        assert 0x008100 <= linked.symbols["routine"] <= 0x00FFBF
        assert data[0:1] == b"\xea"


class TestPinnedAtErrors:
    def test_at_without_address_raises_parse_error(self, caplog: pytest.LogCaptureFixture) -> None:
        assert "invalid expression at `{`" in _failure(".alloc at { .db 1 }", caplog)

    def test_bad_separator_after_name_raises(self, caplog: pytest.LogCaptureFixture) -> None:
        assert "expected one of `in` | `at`, found `via`" in _failure(".alloc foo via 0x8000 { .db 1 }", caplog)


class TestOverlapHardError:
    def test_overlapping_pinned_allocs_fail_build(self, caplog: pytest.LogCaptureFixture) -> None:
        # Two pinned allocs whose ranges overlap fail the link; the
        # diagnostic names both allocs and the bus address they share.
        src = """
        .alloc a at 0x008000 size 0x04 {
            .db 1, 2, 3, 4
        }
        .alloc b at 0x008002 size 0x04 {
            .db 0xA, 0xB, 0xC, 0xD
        }
        """
        assert "shared: 2 bytes from $00:8002" in _failure(src, caplog)
