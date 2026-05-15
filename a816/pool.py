from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Strategy(Enum):
    PACK = "pack"
    ORDER = "order"


class PoolError(Exception):
    pass


class PoolOverflowError(PoolError):
    pass


class PoolOverlapError(PoolError):
    pass


class PoolInvalidRangeError(PoolError):
    pass


@dataclass(frozen=True)
class PoolRange:
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise PoolInvalidRangeError(f"range start 0x{self.start:06x} > end 0x{self.end:06x}")
        if (self.start >> 16) != (self.end >> 16):
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

    @property
    def placed(self) -> bool:
        return self.addr >= 0


@dataclass
class Pool:
    name: str
    ranges: list[PoolRange]
    fill: int = 0x00
    strategy: Strategy = Strategy.PACK
    allocations: list[Allocation] = field(default_factory=list)
    _allocated: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not 0 <= self.fill <= 0xFF:
            raise PoolError(f"fill byte 0x{self.fill:x} out of range")
        self.ranges = _normalize_ranges(self.ranges)

    def request(self, name: str, size: int) -> Allocation:
        if size <= 0:
            raise PoolError(f"alloc '{name}' has non-positive size {size}")
        if self._allocated:
            raise PoolError(f"pool '{self.name}' already allocated; cannot request more")
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
        order = _sort_allocations(self.allocations, self.strategy)
        free = list(self.ranges)
        for alloc in order:
            free = _place(alloc, free)
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
            merged[-1] = PoolRange(start=last.start, end=max(last.end, r.end))
        else:
            merged.append(r)
    return merged


def _sort_allocations(allocs: list[Allocation], strategy: Strategy) -> list[Allocation]:
    if strategy is Strategy.ORDER:
        return list(allocs)
    return sorted(allocs, key=lambda a: (-a.size, a.name))


def _place(alloc: Allocation, free: list[PoolRange]) -> list[PoolRange]:
    for idx, chunk in enumerate(free):
        if chunk.size >= alloc.size:
            alloc.addr = chunk.start
            return _shrink_chunk(free, idx, alloc.size)
    raise PoolOverflowError(f"alloc '{alloc.name}' size {alloc.size} does not fit in any free chunk")


def _shrink_chunk(free: list[PoolRange], idx: int, used: int) -> list[PoolRange]:
    chunk = free[idx]
    remaining_start = chunk.start + used
    tail: list[PoolRange] = []
    if remaining_start <= chunk.end:
        tail.append(PoolRange(start=remaining_start, end=chunk.end))
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
            out.append(PoolRange(start=cursor, end=p_start - 1))
        cursor = max(cursor, p_end + 1)
    if cursor <= r.end:
        out.append(PoolRange(start=cursor, end=r.end))
    return out
