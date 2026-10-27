"""`.for var := lo, hi` runs over `[lo, hi)`: `hi` is excluded.

The docs said `[lo, hi]` while the code always excluded `hi` (Feda's
`.for i := 1, 0xE` ran 13 times). The docs now follow the code, and this
pins it so the two can't drift apart again.
"""

from __future__ import annotations

from tests import link_source


def _bytes(lo: int, hi: int) -> bytes:
    linked = link_source(f"*= 0x008000\n.for i := {lo}, {hi} {{\n    .db i\n}}\n")
    assert linked.exit_code == 0
    return b"".join(linked.data)


def test_hi_is_excluded() -> None:
    assert _bytes(1, 4) == b"\x01\x02\x03"


def test_lo_equal_hi_runs_nothing() -> None:
    assert _bytes(3, 3) == b""


def test_the_documented_example_runs_eight_times() -> None:
    assert _bytes(0, 8) == bytes(range(8))
