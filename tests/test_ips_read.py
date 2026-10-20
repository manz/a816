"""`a816.ips`: reading, applying and extending IPS patches in memory."""

from __future__ import annotations

import pytest

from a816 import ips
from a816.error_codes import E_IPS_MALFORMED
from a816.ips import IpsError


def _patch(*records: bytes) -> bytes:
    return b"PATCH" + b"".join(records) + b"EOF"


def test_records_read_back() -> None:
    patch = _patch(ips.record_bytes(0x10, b"ab"))
    assert [(r.offset, r.data) for r in ips.parse(patch).records] == [(0x10, b"ab")]


def test_an_rle_record_expands() -> None:
    rle = (0x20).to_bytes(3, "big") + b"\x00\x00" + (4).to_bytes(2, "big") + b"\x7f"
    assert ips.parse(_patch(rle)).records[0].data == b"\x7f" * 4


def test_the_end_is_where_eof_starts() -> None:
    patch = _patch(ips.record_bytes(0, b"a"))
    assert patch[ips.parse(patch).end :] == b"EOF"


def test_a_patch_without_magic_is_refused() -> None:
    with pytest.raises(IpsError, match="PATCH"):
        ips.parse(b"NOPE")


def test_a_patch_without_eof_is_refused() -> None:
    with pytest.raises(IpsError, match="no EOF"):
        ips.parse(b"PATCH" + ips.record_bytes(0, b"a"))


def test_the_error_formats_with_its_code() -> None:
    assert "ips error[E0517]" in IpsError(E_IPS_MALFORMED, "bad").format()


def test_apply_writes_and_grows() -> None:
    assert ips.apply(_patch(ips.record_bytes(3, b"xy")), b"abc") == b"abcxy"


def test_apply_overwrites() -> None:
    assert ips.apply(_patch(ips.record_bytes(1, b"Z")), b"abc") == b"aZc"


def test_append_record_goes_before_eof() -> None:
    patch = ips.append_record(_patch(), 5, b"q")
    assert patch == _patch(ips.record_bytes(5, b"q"))


def test_append_keeps_a_truncation_size() -> None:
    patch = ips.append_record(_patch() + b"\x00\x10\x00", 5, b"q")
    assert patch.endswith(b"EOF\x00\x10\x00")


def test_set_last_record_appends() -> None:
    patch = ips.set_last_record(_patch(ips.record_bytes(0, b"a")), 9, b"abcd")
    assert len(ips.parse(patch).records) == 2


def test_set_last_record_replaces_its_own() -> None:
    once = ips.set_last_record(_patch(), 9, b"abcd")
    assert ips.set_last_record(once, 9, b"wxyz") == _patch(ips.record_bytes(9, b"wxyz"))
