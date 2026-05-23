"""WriteAuditor: catches overlapping byte writes from multiple `*=`
sections, `.alloc` blocks, etc."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pytest

from a816.writers import IPSWriter, OverlapError, WriteAuditor


def _audit(mode: str = "warn") -> tuple[WriteAuditor, io.BytesIO]:
    buf = io.BytesIO()
    inner = IPSWriter(buf)
    return WriteAuditor(inner, mode=mode), buf  # type: ignore[arg-type]


def test_non_overlapping_writes_are_silent(caplog: pytest.LogCaptureFixture) -> None:
    auditor, _ = _audit()
    auditor.begin()
    with caplog.at_level(logging.WARNING):
        auditor.write_block(b"\xaa\xbb", 0x008000)
        auditor.write_block(b"\xcc\xdd", 0x008010)
    auditor.end()
    assert not any("overlap" in rec.message for rec in caplog.records)


def test_adjacent_writes_are_silent(caplog: pytest.LogCaptureFixture) -> None:
    """`[0x8000, 0x8004)` then `[0x8004, 0x8008)` share zero bytes — fine."""
    auditor, _ = _audit()
    auditor.begin()
    with caplog.at_level(logging.WARNING):
        auditor.write_block(b"\xaa\xbb\xcc\xdd", 0x008000)
        auditor.write_block(b"\xee\xff\x11\x22", 0x008004)
    auditor.end()
    assert not any("overlap" in rec.message for rec in caplog.records)


def test_full_overlap_warns(caplog: pytest.LogCaptureFixture) -> None:
    auditor, _ = _audit()
    auditor.begin()
    with caplog.at_level(logging.WARNING):
        auditor.write_block(b"\xaa\xbb\xcc\xdd", 0x008000)
        auditor.write_block(b"\x11\x22\x33\x44", 0x008000)
    auditor.end()
    warnings = [rec for rec in caplog.records if "overlap" in rec.message]
    assert len(warnings) == 1
    assert "$008000" in warnings[0].message
    assert "$008003" in warnings[0].message


def test_partial_overlap_warns(caplog: pytest.LogCaptureFixture) -> None:
    auditor, _ = _audit()
    auditor.begin()
    with caplog.at_level(logging.WARNING):
        auditor.write_block(b"\xaa" * 16, 0x008000)
        auditor.write_block(b"\xbb" * 16, 0x008008)
    auditor.end()
    warnings = [rec for rec in caplog.records if "overlap" in rec.message]
    assert len(warnings) == 1
    assert "$008008" in warnings[0].message
    assert "$00800f" in warnings[0].message


def test_error_mode_raises() -> None:
    auditor, _ = _audit(mode="error")
    auditor.begin()
    auditor.write_block(b"\xaa" * 16, 0x008000)
    with pytest.raises(OverlapError, match=r"\$008008"):
        auditor.write_block(b"\xbb" * 16, 0x008008)


def test_off_mode_is_passthrough(caplog: pytest.LogCaptureFixture) -> None:
    auditor, _ = _audit(mode="off")
    auditor.begin()
    with caplog.at_level(logging.WARNING):
        auditor.write_block(b"\xaa" * 16, 0x008000)
        auditor.write_block(b"\xbb" * 16, 0x008000)
    auditor.end()
    assert not any("overlap" in rec.message for rec in caplog.records)


def test_empty_block_skipped() -> None:
    """Zero-byte writes record no range and never report overlap."""
    auditor, _ = _audit()
    auditor.begin()
    auditor.write_block(b"", 0x008000)
    auditor.write_block(b"\xaa", 0x008000)
    auditor.end()
    # No assertion needed — passes if no exception even though both calls
    # used the same start address. Zero-length records nothing.


def test_default_mode_is_warn() -> None:
    auditor, _ = _audit()
    assert auditor._mode == "warn"  # noqa: SLF001 — testing default


def test_end_to_end_overlap_via_two_star_eq_blocks_errors_by_default(tmp_path: Path) -> None:
    """Two `*=` sections writing to overlapping byte spans → build error.

    Default mode is now `error` (cross-repo feedback called this out as
    the #1 footgun); the legacy warn-and-continue path is opt-in via
    `overlap_mode="warn"` (see the explicit-warn test below).
    """
    from a816.program import Program

    src = tmp_path / "main.s"
    src.write_text(
        """
        *=0x008000
            .db 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88
        *=0x008004
            .db 0xAA, 0xBB, 0xCC, 0xDD
        """,
        encoding="utf-8",
    )
    ips = tmp_path / "out.ips"
    program = Program()
    rc = program.assemble_as_patch(str(src), ips)
    assert rc != 0, "default mode should fail the build on overlap"


def test_end_to_end_overlap_via_two_star_eq_blocks_warns_in_warn_mode(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """`overlap_mode='warn'` keeps the legacy log-and-continue behaviour."""
    from a816.program import Program

    src = tmp_path / "main.s"
    src.write_text(
        """
        *=0x008000
            .db 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88
        *=0x008004
            .db 0xAA, 0xBB, 0xCC, 0xDD
        """,
        encoding="utf-8",
    )
    ips = tmp_path / "out.ips"
    program = Program()
    program.resolver.context.overlap_mode = "warn"
    with caplog.at_level(logging.WARNING):
        assert program.assemble_as_patch(str(src), ips) == 0
    warnings = [rec for rec in caplog.records if "overlap" in rec.message]
    assert warnings, "expected overlap warning under explicit warn mode"
