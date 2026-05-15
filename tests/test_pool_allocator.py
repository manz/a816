from __future__ import annotations

import pytest

from a816.pool import (
    Allocation,
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

    def test_zero_size_raises(self) -> None:
        pool = _pool(_range(0x028000, 0x028FFF))
        with pytest.raises(PoolError):
            pool.request("fn", 0)

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
        free = pool._free_ranges()  # noqa: SLF001
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
        chunks = pool._free_ranges()  # noqa: SLF001
        assert chunks[0].start == 0x028080
        assert chunks[1] == _range(0x02A000, 0x02A0FF)


class TestAllocationDataclass:
    def test_unplaced_by_default(self) -> None:
        assert not Allocation(name="x", size=0x100).placed

    def test_placed_after_addr_set(self) -> None:
        alloc = Allocation(name="x", size=0x100)
        alloc.addr = 0x028000
        assert alloc.placed
