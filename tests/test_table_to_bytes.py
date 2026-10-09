"""`Table.to_bytes` encodes exactly as it did before it walked an index.

The rewrite (no tail slicing, only the key lengths the table has, `dict.get`
instead of a KeyError per length) must not move a byte: these pin each rule.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from script import Table

BASE = "01=a\n02=b\n03=ab\n04=abc\n10=\\n\n2021=Cain\n"
# 300 more single kanji: past the first-character fan-out where to_bytes
# leaves the regex scanner for the index walk, so every rule runs on both.
KANJI = "".join(f"{0x4000 + i:04x}={chr(0x4E00 + i)}\n" for i in range(300))


@pytest.fixture(params=["scanner", "index walk"])
def table(request: pytest.FixtureRequest, tmp_path: Path) -> Table:
    (tmp_path / "t.tbl").write_text(BASE + (KANJI if request.param == "index walk" else ""), encoding="utf-8")
    return Table(str(tmp_path / "t.tbl"))


def test_both_strategies_are_exercised(table: Table, request: pytest.FixtureRequest) -> None:
    table.to_bytes("a")

    assert table._use_scanner is (request.node.callspec.params["table"] == "scanner")


def test_the_longest_key_wins(table: Table) -> None:
    assert table.to_bytes("abcab") == b"\x04\x03"


def test_a_shorter_key_after_a_failed_longer_one(table: Table) -> None:
    assert table.to_bytes("abd") == b"\x03"


def test_a_multibyte_value(table: Table) -> None:
    assert table.to_bytes("Cain") == b"\x20\x21"


def test_a_joker_mid_text_is_its_byte(table: Table) -> None:
    assert table.to_bytes("a[0x7f]b") == b"\x01\x7f\x02"


def test_a_joker_over_a_byte_still_raises(table: Table) -> None:
    with pytest.raises(ValueError, match="range"):
        table.to_bytes("[0x123]")


def test_an_unknown_character_is_skipped(table: Table) -> None:
    assert table.to_bytes("a?b") == b"\x01\x02"


def test_an_unclosed_joker_is_skipped_character_by_character(table: Table) -> None:
    assert table.to_bytes("[0xa") == b"\x01"


def test_a_newline_key(table: Table) -> None:
    assert table.to_bytes("a\nb") == b"\x01\x10\x02"


def test_keys_added_after_encoding_are_used(table: Table) -> None:
    """The cached key lengths follow the table."""
    table.to_bytes("a")
    table.add_lookup("abcd", [0x05])
    table.max_text_length = 4

    assert table.to_bytes("abcd") == b"\x05"


def test_a_table_never_included_encodes_only_jokers() -> None:
    """`max_text_length` is only set by `include`; without it no key was tried."""
    table = Table()
    table.parse_table_line("01=a")

    assert table.to_bytes("a[0x02]") == b"\x02"
