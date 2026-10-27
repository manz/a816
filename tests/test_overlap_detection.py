"""Overlapping byte writes from two `*=` sections, caught at link per
`overlap_mode`: `error` (the default) fails the build, `warn` logs and
continues, `off` skips the check."""

from __future__ import annotations

import logging

import pytest

from tests import link_source

_OVERLAPPING = """
*=0x008000
    .db 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88
*=0x008004
    .db 0xAA, 0xBB, 0xCC, 0xDD
"""


def test_two_star_eq_blocks_overlapping_fail_by_default() -> None:
    assert link_source(_OVERLAPPING).exit_code != 0


def test_two_star_eq_blocks_overlapping_build_in_warn_mode() -> None:
    assert link_source(_OVERLAPPING, overlap_mode="warn").exit_code == 0


def test_warn_mode_logs_the_overlap(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        link_source(_OVERLAPPING, overlap_mode="warn")
    assert any("overlap" in rec.message for rec in caplog.records)


def test_off_mode_builds_without_a_word(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        assert link_source(_OVERLAPPING, overlap_mode="off").exit_code == 0
    assert not any("overlap" in rec.message for rec in caplog.records)
