from __future__ import annotations

import pytest

from a816.pool import (
    Allocation,
    OverflowKind,
    Pool,
    PoolError,
    PoolInvalidRangeError,
    PoolOverflowError,
    PoolOverlapError,
    PoolRange,
    Strategy,
)


def _range(start: int, end: int) -> PoolRange:
    return PoolRange(start=start, end=end)


def _pool(
    *ranges: PoolRange,
    name: str = "p",
    fill: int = 0x00,
    strategy: Strategy = Strategy.PACK,
) -> Pool:
    return Pool(name=name, ranges=list(ranges), fill=fill, strategy=strategy)


class TestPoolRange:
    def test_size_inclusive(self) -> None:
        assert _range(0x028000, 0x0280FF).size == 0x100

    def test_start_gt_end_raises(self) -> None:
        with pytest.raises(PoolInvalidRangeError):
            _range(0x028100, 0x028000)

    def test_range_crossing_bank_raises(self) -> None:
        with pytest.raises(PoolInvalidRangeError):
            _range(0x02FF00, 0x030100)

    def test_overlap_detected(self) -> None:
        assert _range(0x028000, 0x028100).overlaps(_range(0x028080, 0x028200))

    def test_overlap_disjoint_false(self) -> None:
        assert not _range(0x028000, 0x0280FF).overlaps(_range(0x028100, 0x0281FF))

    def test_adjacent_detected(self) -> None:
        assert _range(0x028000, 0x0280FF).adjacent(_range(0x028100, 0x0281FF))


class TestPoolConstruction:
    def test_invalid_fill_raises(self) -> None:
        with pytest.raises(PoolError):
            Pool(name="p", ranges=[_range(0x028000, 0x028FFF)], fill=0x100)

    def test_overlapping_ranges_raise(self) -> None:
        with pytest.raises(PoolOverlapError):
            _pool(_range(0x028000, 0x028200), _range(0x028100, 0x028300))

    def test_adjacent_ranges_merge(self) -> None:
        pool = _pool(_range(0x028000, 0x0280FF), _range(0x028100, 0x0281FF))
        assert len(pool.ranges) == 1
        assert pool.ranges[0] == _range(0x028000, 0x0281FF)

    def test_adjacent_across_banks_dont_merge(self) -> None:
        pool = _pool(_range(0x02FFFF, 0x02FFFF), _range(0x030000, 0x030000))
        assert len(pool.ranges) == 2

    def test_ranges_normalized_in_order(self) -> None:
        pool = _pool(_range(0x028200, 0x0282FF), _range(0x028000, 0x0280FF))
        assert pool.ranges[0].start == 0x028000
        assert pool.ranges[1].start == 0x028200


class TestAllocator:
    def test_single_chunk_single_alloc_fits(self) -> None:
        pool = _pool(_range(0x028000, 0x028FFF))
        alloc = pool.request("fn", 0x100)
        pool.allocate()
        assert alloc.addr == 0x028000
        assert alloc.placed

    def test_single_chunk_overflow_raises(self) -> None:
        pool = _pool(_range(0x028000, 0x0280FF))
        pool.request("fn", 0x200)
        with pytest.raises(PoolOverflowError):
            pool.allocate()

    def test_pack_largest_first(self) -> None:
        pool = _pool(_range(0x028000, 0x028FFF), strategy=Strategy.PACK)
        small = pool.request("small", 0x100)
        big = pool.request("big", 0x800)
        pool.allocate()
        assert big.addr < small.addr

    def test_order_declaration_order(self) -> None:
        pool = _pool(_range(0x028000, 0x028FFF), strategy=Strategy.ORDER)
        first = pool.request("first", 0x100)
        second = pool.request("second", 0x800)
        pool.allocate()
        assert first.addr == 0x028000
        assert second.addr == 0x028100

    def test_first_fit_picks_first_chunk_with_room(self) -> None:
        pool = _pool(
            _range(0x028000, 0x0280FF),
            _range(0x02A000, 0x02AFFF),
            strategy=Strategy.ORDER,
        )
        alloc = pool.request("fn", 0x200)
        pool.allocate()
        assert alloc.addr == 0x02A000

    def test_alloc_exactly_fills_chunk(self) -> None:
        pool = _pool(_range(0x028000, 0x0280FF))
        alloc = pool.request("fn", 0x100)
        pool.allocate()
        assert alloc.addr == 0x028000
        assert pool.free == 0
        assert pool.fragments == 0

    def test_zero_size_takes_no_space(self) -> None:
        """An empty body binds its address and leaves room for the next block."""
        pool = _pool(_range(0x028000, 0x028FFF))
        empty = pool.request("empty", 0)
        pool.request("after", 4)
        pool.allocate()
        assert empty.placed
        assert pool.free == pool.capacity - 4

    def test_negative_size_raises(self) -> None:
        pool = _pool(_range(0x028000, 0x028FFF))
        with pytest.raises(PoolError):
            pool.request("fn", -1)

    def test_pack_deterministic(self) -> None:
        results: list[list[int]] = []
        for _ in range(3):
            pool = _pool(_range(0x028000, 0x028FFF))
            allocs = [pool.request(f"f{i}", size) for i, size in enumerate([0x80, 0x200, 0x40, 0x100])]
            pool.allocate()
            results.append([a.addr for a in allocs])
        assert results[0] == results[1] == results[2]

    def test_order_deterministic(self) -> None:
        results: list[list[int]] = []
        for _ in range(3):
            pool = _pool(_range(0x028000, 0x028FFF), strategy=Strategy.ORDER)
            allocs = [pool.request(f"f{i}", size) for i, size in enumerate([0x80, 0x200, 0x40, 0x100])]
            pool.allocate()
            results.append([a.addr for a in allocs])
        assert results[0] == results[1] == results[2]


class TestPoolStats:
    def test_capacity_equals_sum_of_ranges(self) -> None:
        pool = _pool(_range(0x028000, 0x0280FF), _range(0x02A000, 0x02A0FF))
        assert pool.capacity == 0x200

    def test_free_used_sum_equals_capacity(self) -> None:
        pool = _pool(_range(0x028000, 0x028FFF))
        pool.request("a", 0x100)
        pool.request("b", 0x200)
        pool.allocate()
        assert pool.used + pool.free == pool.capacity

    def test_largest_chunk_after_alloc(self) -> None:
        pool = _pool(_range(0x028000, 0x028FFF), strategy=Strategy.ORDER)
        pool.request("a", 0x100)
        pool.allocate()
        assert pool.largest_chunk == 0x0F00

    def test_fragments_count(self) -> None:
        pool = _pool(
            _range(0x028000, 0x0280FF),
            _range(0x02A000, 0x02A0FF),
            strategy=Strategy.ORDER,
        )
        pool.request("a", 0x80)
        pool.allocate()
        assert pool.fragments == 2  # tail of chunk1 + all of chunk2

    def test_stats_before_allocate(self) -> None:
        pool = _pool(_range(0x028000, 0x028FFF))
        pool.request("a", 0x100)
        assert pool.used == 0
        assert pool.free == 0x1000


class TestReclaim:
    def test_reclaim_extends_capacity(self) -> None:
        pool = _pool(_range(0x028000, 0x0280FF))
        before = pool.capacity
        pool.reclaim(_range(0x02A000, 0x02A0FF))
        assert pool.capacity == before + 0x100

    def test_reclaim_overlapping_raises(self) -> None:
        pool = _pool(_range(0x028000, 0x0280FF))
        with pytest.raises(PoolOverlapError):
            pool.reclaim(_range(0x028080, 0x028180))

    def test_reclaim_adjacent_merges(self) -> None:
        pool = _pool(_range(0x028000, 0x0280FF))
        pool.reclaim(_range(0x028100, 0x0281FF))
        assert len(pool.ranges) == 1
        assert pool.ranges[0] == _range(0x028000, 0x0281FF)

    def test_reclaim_after_allocate_raises(self) -> None:
        pool = _pool(_range(0x028000, 0x028FFF))
        pool.request("a", 0x100)
        pool.allocate()
        with pytest.raises(PoolError):
            pool.reclaim(_range(0x02A000, 0x02A0FF))

    def test_reclaim_then_alloc_uses_new_range(self) -> None:
        pool = _pool(_range(0x028000, 0x0280FF), strategy=Strategy.ORDER)
        pool.reclaim(_range(0x02A000, 0x02AFFF))
        alloc = pool.request("fn", 0x200)
        pool.allocate()
        assert alloc.addr == 0x02A000


class TestIdempotence:
    def test_allocate_twice_is_noop(self) -> None:
        pool = _pool(_range(0x028000, 0x028FFF))
        alloc = pool.request("fn", 0x100)
        pool.allocate()
        addr_first = alloc.addr
        pool.allocate()
        assert alloc.addr == addr_first

    def test_request_after_allocate_raises(self) -> None:
        pool = _pool(_range(0x028000, 0x028FFF))
        pool.request("a", 0x100)
        pool.allocate()
        with pytest.raises(PoolError):
            pool.request("b", 0x100)


class TestFreeRangesEdgeCases:
    def test_free_ranges_before_allocate_returns_full_ranges(self) -> None:
        pool = _pool(_range(0x028000, 0x0280FF))
        free = pool._free_ranges()
        assert free == [_range(0x028000, 0x0280FF)]

    def test_subtract_one_placement_outside_range(self) -> None:
        # Two ranges, alloc lands in first, second untouched but checked.
        pool = _pool(
            _range(0x028000, 0x0280FF),
            _range(0x02A000, 0x02A0FF),
            strategy=Strategy.ORDER,
        )
        pool.request("a", 0x80)
        pool.allocate()
        # The unused tail of chunk 1 plus all of chunk 2 should be reported.
        assert pool.fragments == 2
        chunks = pool._free_ranges()
        assert chunks[0].start == 0x028080
        assert chunks[1] == _range(0x02A000, 0x02A0FF)


class TestAllocationDataclass:
    def test_unplaced_by_default(self) -> None:
        assert not Allocation(name="x", size=0x100).placed

    def test_placed_after_addr_set(self) -> None:
        alloc = Allocation(name="x", size=0x100)
        alloc.addr = 0x028000
        assert alloc.placed


def _two_bank_pool() -> Pool:
    """16 bytes at the end of bank $01 + 16 bytes at the start of bank $02."""
    return _pool(_range(0x01FFF0, 0x01FFFF), _range(0x028000, 0x02800F), name="slack")


def _overflow(pool: Pool) -> PoolOverflowError:
    with pytest.raises(PoolOverflowError) as exc_info:
        pool.allocate()
    return exc_info.value


def _too_large(pool: Pool, size: int = 20) -> PoolOverflowError:
    pool.request("big", size)
    return _overflow(pool)


def _larger_than_any_range() -> PoolOverflowError:
    return _too_large(_two_bank_pool())


def _larger_than_single_range_pool() -> PoolOverflowError:
    return _too_large(_pool(_range(0x008000, 0x008001), name="slot"), size=3)


def _larger_than_any_same_bank_range() -> PoolOverflowError:
    return _too_large(_pool(_range(0x028000, 0x028007), _range(0x028010, 0x028017)))


def _larger_than_merged_adjacent_ranges() -> PoolOverflowError:
    return _too_large(_pool(_range(0x028000, 0x028007), _range(0x028008, 0x02800F)))


def _fragmented() -> PoolOverflowError:
    pool = _two_bank_pool()
    pool.request("a", 12)
    pool.request("b", 12)
    pool.request("c", 8)
    return _overflow(pool)


def _exhausted() -> PoolOverflowError:
    pool = _two_bank_pool()
    pool.request("a", 12)
    pool.request("b", 12)
    pool.request("c", 10)
    return _overflow(pool)


class TestOverflowKind:
    def test_larger_than_any_range_is_too_large(self) -> None:
        assert _larger_than_any_range().kind is OverflowKind.TOO_LARGE

    def test_fragmented_is_fragmented(self) -> None:
        assert _fragmented().kind is OverflowKind.FRAGMENTED

    def test_exhausted_is_exhausted(self) -> None:
        assert _exhausted().kind is OverflowKind.EXHAUSTED


class TestOverflowMessages:
    def test_larger_than_any_range_names_the_range_limit(self) -> None:
        assert "larger than its largest range (16 bytes)" in str(_larger_than_any_range())

    def test_larger_than_any_range_names_the_alloc(self) -> None:
        assert str(_larger_than_any_range()).startswith("alloc 'big' (20 bytes)")

    def test_larger_than_any_range_explains_bank_rule(self) -> None:
        assert "a block never spans a bank boundary or two separate ranges" in str(_larger_than_any_range())

    def test_same_bank_ranges_explain_the_range_rule(self) -> None:
        assert str(_larger_than_any_same_bank_range()).endswith("a block never spans two separate ranges")

    def test_same_bank_ranges_do_not_blame_a_bank_boundary(self) -> None:
        assert "bank" not in str(_larger_than_any_same_bank_range())

    def test_merged_adjacent_ranges_report_the_merged_pool_size(self) -> None:
        assert str(_larger_than_merged_adjacent_ranges()).endswith("larger than the pool (16 bytes)")

    def test_larger_than_any_range_carries_largest_range(self) -> None:
        assert _larger_than_any_range().largest_range == 16

    def test_larger_than_any_range_hint_suggests_splitting(self) -> None:
        assert "split 'big'" in _larger_than_any_range().hint

    def test_fragmented_says_total_free_suffices(self) -> None:
        assert "8 bytes free in total but fragmented" in str(_fragmented())

    def test_fragmented_names_largest_free_chunk(self) -> None:
        assert "largest free chunk is 4 bytes" in str(_fragmented())

    def test_fragmented_carries_total_free(self) -> None:
        assert _fragmented().total_free == 8

    def test_fragmented_hint_is_the_action(self) -> None:
        assert _fragmented().hint == "split 'c' or grow one of the ranges"

    def test_exhausted_names_largest_free_chunk(self) -> None:
        assert "largest free chunk is 4 bytes" in str(_exhausted())

    def test_exhausted_is_not_reported_as_fragmented(self) -> None:
        assert "fragmented" not in str(_exhausted())

    def test_exhausted_hint_suggests_growing_the_pool(self) -> None:
        assert "grow pool 'slack'" in _exhausted().hint

    def test_larger_than_any_range_hint_suggests_growing_a_range(self) -> None:
        assert "grow a range to at least 20 bytes" in _larger_than_any_range().hint

    def test_larger_than_single_range_pool_reports_pool_size(self) -> None:
        expected = "alloc 'big' (3 bytes) does not fit in pool 'slot': larger than the pool (2 bytes)"
        assert str(_larger_than_single_range_pool()) == expected

    def test_larger_than_single_range_pool_hint_suggests_growing_the_pool(self) -> None:
        assert "grow the pool to at least 3 bytes" in _larger_than_single_range_pool().hint


def test_block_never_spans_ranges_contiguous_across_a_bank_boundary() -> None:
    """$01:FFF0-$01:FFFF and $02:0000-$02:000F are contiguous addresses, yet no 20-byte block fits."""
    pool = _pool(_range(0x01FFF0, 0x01FFFF), _range(0x020000, 0x02000F))
    pool.request("big", 20)
    with pytest.raises(PoolOverflowError):
        pool.allocate()
