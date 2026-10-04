from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("a816.pool")


class Strategy(Enum):
    PACK = "pack"
    ORDER = "order"


class PoolError(Exception):
    pass


class OverflowKind(Enum):
    """Why an alloc found no free chunk large enough."""

    TOO_LARGE = "too_large"
    """Larger than every range of the pool: no amount of free space helps."""
    FRAGMENTED = "fragmented"
    """Enough bytes free in total, but no single chunk holds them."""
    EXHAUSTED = "exhausted"
    """The pool is simply out of room."""


class PoolOverflowError(PoolError):
    """An allocation found no free chunk large enough in its pool.

    `kind` classifies the overflow once; message and hint both dispatch on it.
    Ranges here are the pool's normalised ranges: adjacent same-bank ranges
    are already merged, so a block never spans two *separate* ranges, and
    ranges in different banks never merge, so it never spans a bank boundary.
    A single-range pool (e.g. the one synthesised for `.alloc at ADDR size N`)
    reports the pool size instead of the largest range.
    """

    def __init__(
        self,
        pool_name: str,
        alloc_name: str,
        size: int,
        largest_free: int,
        largest_range: int,
        total_free: int,
        single_range: bool,
        spans_banks: bool,
    ) -> None:
        self.pool_name = pool_name
        self.alloc_name = alloc_name
        self.size = size
        self.largest_free = largest_free
        self.largest_range = largest_range
        self.total_free = total_free
        self.single_range = single_range
        self.spans_banks = spans_banks
        super().__init__(self._message())

    @property
    def kind(self) -> OverflowKind:
        if self.size > self.largest_range:
            return OverflowKind.TOO_LARGE
        if self.total_free >= self.size:
            return OverflowKind.FRAGMENTED
        return OverflowKind.EXHAUSTED

    def _message(self) -> str:
        head = f"alloc '{self.alloc_name}' ({self.size} bytes) does not fit in pool '{self.pool_name}'"
        kind = self.kind
        if kind is OverflowKind.TOO_LARGE:
            return f"{head}: {self._too_large_reason()}"
        if kind is OverflowKind.FRAGMENTED:
            return (
                f"{head}: {self.total_free} bytes free in total but fragmented; "
                f"largest free chunk is {self.largest_free} bytes"
            )
        return f"{head}: largest free chunk is {self.largest_free} bytes"

    def _too_large_reason(self) -> str:
        if self.single_range:
            return f"larger than the pool ({self.largest_range} bytes)"
        rule = "a bank boundary or two separate ranges" if self.spans_banks else "two separate ranges"
        return f"larger than its largest range ({self.largest_range} bytes); a block never spans {rule}"

    @property
    def hint(self) -> str:
        kind = self.kind
        if kind is OverflowKind.TOO_LARGE:
            target = "the pool" if self.single_range else "a range"
            return f"split '{self.alloc_name}' into smaller allocs or grow {target} to at least {self.size} bytes"
        if kind is OverflowKind.FRAGMENTED:
            return f"split '{self.alloc_name}' or grow one of the ranges"
        return f"grow pool '{self.pool_name}' or move code out of it"


class PoolOverlapError(PoolError):
    pass


class PoolInvalidRangeError(PoolError):
    pass


@dataclass(frozen=True)
class PoolRange:
    start: int
    end: int
    allow_bank_cross: bool = False

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise PoolInvalidRangeError(f"range start 0x{self.start:06x} > end 0x{self.end:06x}")
        # `allow_bank_cross=True` opts out of the bank-boundary check.
        # Used by synthesised pools for unbounded `.alloc at ADDR { ... }`
        # (the `*= ADDR` desugar shape) so a huge `.incbin` payload that
        # legitimately spans `$XX:FFFF → $XX+1:0000` doesn't get rejected
        # before the allocator even sees it. User-declared `.pool` ranges
        # keep the strict bank-local guard.
        if not self.allow_bank_cross and (self.start >> 16) != (self.end >> 16):
            raise PoolInvalidRangeError(f"range 0x{self.start:06x}..0x{self.end:06x} crosses bank boundary")

    @property
    def size(self) -> int:
        return self.end - self.start + 1

    def overlaps(self, other: PoolRange) -> bool:
        return not (self.end < other.start or other.end < self.start)

    def adjacent(self, other: PoolRange) -> bool:
        return self.end + 1 == other.start or other.end + 1 == self.start


@dataclass
class Allocation:
    name: str
    size: int
    addr: int = -1
    pinned_addr: int = -1
    """Fixed-address request (`.reserve NAME SIZE at ADDR in POOL`): the
    allocator places this span *at* `pinned_addr` (validating it lies within
    a pool range and overlaps nothing else, then carving it) before floating
    allocations are placed. Kept separate from `addr` so `placed` stays False
    until `allocate()` runs (object mode binds body labels at the sandbox base
    uniformly and lets the linker apply the final address)."""

    @property
    def pinned(self) -> bool:
        return self.pinned_addr >= 0

    @property
    def placed(self) -> bool:
        return self.addr >= 0


@dataclass
class Pool:
    name: str
    ranges: list[PoolRange]
    fill: int = 0x00
    strategy: Strategy = Strategy.PACK
    bss: bool = False
    """Byte-less pool: allocations reserve + overlap-check address space (WRAM,
    SRAM, custom RAM maps) but emit nothing into the image. Bodies may only
    reserve (`.res`) / label / assign; emitting a byte is an error."""
    allocations: list[Allocation] = field(default_factory=list)
    _allocated: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not 0 <= self.fill <= 0xFF:
            raise PoolError(f"fill byte 0x{self.fill:x} out of range")
        self.ranges = _normalize_ranges(self.ranges)

    def request(self, name: str, size: int, addr: int | None = None) -> Allocation:
        if size <= 0:
            raise PoolError(f"alloc '{name}' has non-positive size {size}")
        if self._allocated:
            raise PoolError(f"pool '{self.name}' already allocated; cannot request more")
        if addr is not None:
            alloc = Allocation(name=name, size=size, pinned_addr=addr)
        else:
            alloc = Allocation(name=name, size=size)
        self.allocations.append(alloc)
        return alloc

    def reclaim(self, r: PoolRange) -> None:
        if self._allocated:
            raise PoolError(f"pool '{self.name}' already allocated; cannot reclaim")
        for existing in self.ranges:
            if existing.overlaps(r):
                raise PoolOverlapError(
                    f"reclaim 0x{r.start:06x}..0x{r.end:06x} overlaps existing "
                    f"0x{existing.start:06x}..0x{existing.end:06x}"
                )
        self.ranges = _normalize_ranges([*self.ranges, r])

    def allocate(self) -> None:
        if self._allocated:
            return
        pinned = [a for a in self.allocations if a.pinned]
        floating = [a for a in self.allocations if not a.pinned]
        order = _sort_allocations(floating, self.strategy)
        free = list(self.ranges)
        total = sum(r.size for r in self.ranges)
        logger.info(
            "pool %s: %d pinned + %d floating alloc(s) into %d range(s) totaling %d bytes",
            self.name,
            len(pinned),
            len(order),
            len(self.ranges),
            total,
        )
        for alloc in sorted(pinned, key=lambda a: a.pinned_addr):
            alloc.addr = alloc.pinned_addr
            free = _carve(alloc, free, self.ranges, self.name)
            free_total = sum(r.size for r in free)
            logger.info(
                "  pinned %s size %d at 0x%06x  (free: %d bytes across %d range(s))",
                alloc.name,
                alloc.size,
                alloc.addr,
                free_total,
                len(free),
            )
        for alloc in order:
            free = _place(alloc, free, self.ranges, self.name)
            free_total = sum(r.size for r in free)
            logger.info(
                "  placed %s size %d at 0x%06x  (free: %d bytes across %d range(s))",
                alloc.name,
                alloc.size,
                alloc.addr,
                free_total,
                len(free),
            )
        for r in free:
            logger.info(
                "  pool %s leftover 0x%06x..0x%06x (%d bytes)",
                self.name,
                r.start,
                r.end,
                r.size,
            )
        self._allocated = True

    @property
    def capacity(self) -> int:
        return sum(r.size for r in self.ranges)

    @property
    def used(self) -> int:
        return sum(a.size for a in self.allocations if a.placed)

    @property
    def free(self) -> int:
        return self.capacity - self.used

    @property
    def fragments(self) -> int:
        return len(self._free_ranges())

    @property
    def largest_chunk(self) -> int:
        chunks = self._free_ranges()
        return max((r.size for r in chunks), default=0)

    def _free_ranges(self) -> list[PoolRange]:
        if not self._allocated:
            return list(self.ranges)
        placed = sorted(
            ((a.addr, a.addr + a.size - 1) for a in self.allocations if a.placed),
            key=lambda p: p[0],
        )
        return _subtract(self.ranges, placed)


def _normalize_ranges(ranges: list[PoolRange]) -> list[PoolRange]:
    if not ranges:
        return []
    ordered = sorted(ranges, key=lambda r: r.start)
    merged: list[PoolRange] = [ordered[0]]
    for r in ordered[1:]:
        last = merged[-1]
        if last.overlaps(r):
            raise PoolOverlapError(
                f"ranges 0x{last.start:06x}..0x{last.end:06x} and 0x{r.start:06x}..0x{r.end:06x} overlap"
            )
        if last.adjacent(r) and (last.start >> 16) == (r.start >> 16):
            merged[-1] = PoolRange(
                start=last.start,
                end=max(last.end, r.end),
                allow_bank_cross=last.allow_bank_cross or r.allow_bank_cross,
            )
        else:
            merged.append(r)
    return merged


def _sort_allocations(allocs: list[Allocation], strategy: Strategy) -> list[Allocation]:
    if strategy is Strategy.ORDER:
        return list(allocs)
    return sorted(allocs, key=lambda a: (-a.size, a.name))


def _place(alloc: Allocation, free: list[PoolRange], ranges: list[PoolRange], pool_name: str) -> list[PoolRange]:
    for idx, chunk in enumerate(free):
        if chunk.size >= alloc.size:
            alloc.addr = chunk.start
            return _shrink_chunk(free, idx, alloc.size)
    raise PoolOverflowError(
        pool_name,
        alloc.name,
        alloc.size,
        largest_free=max((chunk.size for chunk in free), default=0),
        largest_range=max((r.size for r in ranges), default=0),
        total_free=sum(chunk.size for chunk in free),
        single_range=len(ranges) == 1,
        spans_banks=len({r.start >> 16 for r in ranges}) > 1,
    )


def _carve(alloc: Allocation, free: list[PoolRange], ranges: list[PoolRange], pool_name: str) -> list[PoolRange]:
    span_start = alloc.addr
    span_end = alloc.addr + alloc.size - 1
    for idx, chunk in enumerate(free):
        if span_start < chunk.start or span_end > chunk.end:
            continue
        # span fits wholly inside this free chunk: split off head + tail.
        out: list[PoolRange] = list(free[:idx])
        if span_start > chunk.start:
            out.append(PoolRange(start=chunk.start, end=span_start - 1, allow_bank_cross=chunk.allow_bank_cross))
        if span_end < chunk.end:
            out.append(PoolRange(start=span_end + 1, end=chunk.end, allow_bank_cross=chunk.allow_bank_cross))
        out.extend(free[idx + 1 :])
        return out
    # No free chunk contains the span. If a declared pool range does contain it,
    # the conflict is with another (already-carved) allocation; otherwise the
    # address simply lies outside the pool.
    if any(r.start <= span_start and span_end <= r.end for r in ranges):
        raise PoolOverlapError(
            f"pinned alloc '{alloc.name}' 0x{span_start:06x}..0x{span_end:06x} "
            f"overlaps another allocation in pool '{pool_name}'"
        )
    raise PoolInvalidRangeError(
        f"pinned alloc '{alloc.name}' 0x{span_start:06x}..0x{span_end:06x} is outside the ranges of pool '{pool_name}'"
    )


def _shrink_chunk(free: list[PoolRange], idx: int, used: int) -> list[PoolRange]:
    chunk = free[idx]
    remaining_start = chunk.start + used
    tail: list[PoolRange] = []
    if remaining_start <= chunk.end:
        tail.append(PoolRange(start=remaining_start, end=chunk.end, allow_bank_cross=chunk.allow_bank_cross))
    return [*free[:idx], *tail, *free[idx + 1 :]]


def _subtract(ranges: list[PoolRange], placed: list[tuple[int, int]]) -> list[PoolRange]:
    result: list[PoolRange] = []
    for r in ranges:
        result.extend(_subtract_one(r, placed))
    return result


def _subtract_one(r: PoolRange, placed: list[tuple[int, int]]) -> list[PoolRange]:
    cursor = r.start
    out: list[PoolRange] = []
    for p_start, p_end in placed:
        if p_end < r.start or p_start > r.end:
            continue
        if p_start > cursor:
            out.append(PoolRange(start=cursor, end=p_start - 1, allow_bank_cross=r.allow_bank_cross))
        cursor = max(cursor, p_end + 1)
    if cursor <= r.end:
        out.append(PoolRange(start=cursor, end=r.end, allow_bank_cross=r.allow_bank_cross))
    return out
