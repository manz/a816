"""Behavioural tests for `a816.section`.

Sections are the unifying placement primitive that supersedes the
historical `Region` concept. These tests pin the construction +
validation contract before the rest of the codebase migrates onto it.

Companion tests for cross-section overlap, pool feeding, and `.alloc
… at ADDR[..END]` codegen live in dedicated suites under PR1 follow-up
commits.
"""

from __future__ import annotations

import pytest

from a816.section import Placement, Section


class TestPinnedSection:
    def test_unbounded_pinned_is_not_marked_bounded(self) -> None:
        sec = Section(name="reset", placement=Placement.PINNED, code=b"\x60", base_address=0x008000)
        assert sec.is_bounded is False
        assert sec.overflows() is False

    def test_bounded_pinned_within_range(self) -> None:
        # Body of 3 bytes at $00:FFFE..$00:0000 — wait, sticking to in-range case:
        # 2 bytes at $00:FFFE..$00:FFFF (end inclusive).
        sec = Section(
            name="vectors",
            placement=Placement.PINNED,
            code=b"\x00\x80",
            base_address=0x00FFFE,
            end_address=0x00FFFF,
        )
        assert sec.is_bounded is True
        assert sec.overflows() is False

    def test_bounded_pinned_detects_overflow(self) -> None:
        sec = Section(
            name="oversized",
            placement=Placement.PINNED,
            code=b"\xea\xea\xea",
            base_address=0x00FFFE,
            end_address=0x00FFFF,
        )
        assert sec.overflows() is True

    def test_pinned_rejects_pool_name(self) -> None:
        with pytest.raises(ValueError, match="PINNED placement cannot carry a pool_name"):
            Section(
                name="bad",
                placement=Placement.PINNED,
                code=b"",
                pool_name="engine",
            )


class TestPooledSection:
    def test_pooled_with_pool_name(self) -> None:
        sec = Section(
            name="engine_update",
            placement=Placement.POOLED,
            code=b"\x60",
            pool_name="engine",
        )
        assert sec.pool_name == "engine"
        # POOLED is implicitly bounded by its pool's declared ranges, not by
        # an explicit end_address on the section itself.
        assert sec.is_bounded is True
        assert sec.overflows() is False  # POOLED handles capacity via PoolOverflowError

    def test_pooled_requires_pool_name(self) -> None:
        with pytest.raises(ValueError, match="POOLED placement requires a pool_name"):
            Section(
                name="bad",
                placement=Placement.POOLED,
                code=b"",
                pool_name=None,
            )

    def test_pooled_rejects_end_address(self) -> None:
        with pytest.raises(ValueError, match="POOLED placement is bounded by its pool"):
            Section(
                name="bad",
                placement=Placement.POOLED,
                code=b"",
                pool_name="engine",
                end_address=0x010000,
            )


class TestSectionSize:
    def test_empty_section_size_is_zero(self) -> None:
        sec = Section(name="empty", placement=Placement.PINNED, code=b"")
        assert sec.size == 0

    def test_size_reflects_code_length(self) -> None:
        sec = Section(name="body", placement=Placement.PINNED, code=b"\xea" * 42)
        assert sec.size == 42
