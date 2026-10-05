from typing import Any


class BusRegion:
    """Nominal base for bus regions: legacy `.map` strides and bsnes regions."""

    bank_range: tuple[int, int]
    address_range: tuple[int, int]
    mask: int
    writable: bool

    def physical_address(self, value: int) -> int | None:
        raise NotImplementedError

    def logical_address(self, value: int, near: int | None = None) -> int:
        """Logical address of file offset ``value``; ``near`` picks the bank range a caller is already in."""
        raise NotImplementedError


class Mapping(BusRegion):
    def __init__(
        self,
        bank_range: tuple[int, int],
        address_range: tuple[int, int],
        mask: int,
        writeable: bool = False,
        mirror: tuple[int, int] | None = None,
    ) -> None:
        self.bank_range = bank_range
        self.mirror = mirror
        self.address_range = address_range
        self.mask = mask
        self.writable = writeable

    def physical_address(self, value: int) -> int | None:
        bank = value >> 16
        if self.writable is False:
            return (bank - self.bank_range[0]) * self.mask + (value & ~self.mask & 0xFFFF)
        else:
            return None

    def logical_address(self, value: int, near: int | None = None) -> int:
        bank = value // self.mask

        return (bank + self.bank_range[0]) << 16 | (self.mask & 0xFFFF) + value % self.mask


def reduce(addr: int, mask: int) -> int:
    """bsnes `Bus::reduce`: delete every `mask` bit from `addr`, compacting the rest."""
    while mask:
        bits = (mask & -mask) - 1
        addr = ((addr >> 1) & ~bits) | (addr & bits)
        mask = (mask & (mask - 1)) >> 1
    return addr


def mirror(addr: int, size: int) -> int:
    """bsnes `Bus::mirror`: fold `addr` into a memory of `size` bytes (any size, not just powers of two)."""
    if size == 0:
        return 0
    base = 0
    bit = 1 << 23
    while addr >= size:
        while not addr & bit:
            bit >>= 1
        addr -= bit
        if size > bit:
            size -= bit
            base += bit
        bit >>= 1
    return base + addr


def _expand(value: int, mask: int) -> int:
    """Inverse of `reduce` with the masked bits cleared: spread `value` over the unmasked bit positions."""
    out = 0
    src = 0
    for pos in range(24):
        if not mask >> pos & 1:
            out |= (value >> src & 1) << pos
            src += 1
    return out | (value >> src) << 24


def parse_bml_address(spec: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Split a BML `map address=` value (`00-7d,80-ff:8000-ffff`) into bank ranges and address windows.

    Both sides take comma-separated ranges (`00-3f,80-bf:6000-6bff,7000-7bff`).

    Raises:
        ValueError: `spec` is not `BANKS:ADDRESSES` with hex ranges.
    """
    banks, sep, window = spec.partition(":")
    if not sep:
        raise ValueError(f"expected BANKS:ADDRESSES, got {spec!r}")
    ranges = [_hex_range(part, 0xFF, spec) for part in banks.split(",")]
    return ranges, [_hex_range(part, 0xFFFF, spec) for part in window.split(",")]


def _hex_range(text: str, limit: int, spec: str) -> tuple[int, int]:
    lo_text, _, hi_text = text.strip().partition("-")
    try:
        lo = int(lo_text, 16)
        hi = int(hi_text, 16) if hi_text else lo
    except ValueError:
        raise ValueError(f"bad hex range {text!r} in {spec!r}") from None
    if not 0 <= lo <= hi <= limit:
        raise ValueError(f"range {text!r} out of order or above ${limit:X} in {spec!r}")
    return lo, hi


class BsnesRegion(BusRegion):
    """A bus region with bsnes semantics, as written in `boards.bml`.

    `physical = base + mirror(reduce(addr, mask), size - base)` over the full
    24-bit address. Read/write RAM has no file offset. The inverse picks the
    bank range the caller is already in (`near`), else the first listed one.
    """

    def __init__(
        self,
        ranges: list[tuple[int, int]],
        windows: list[tuple[int, int]],
        mask: int,
        base: int,
        size: int,
        writable: bool,
    ) -> None:
        self.ranges = ranges
        self.windows = windows
        self.mask = mask
        self.base = base
        self.size = size
        self.writable = writable
        # Legacy-shaped views (xdds, diagnostics): the first bank range and window.
        self.bank_range = ranges[0]
        self.address_range = windows[0]

    def physical_address(self, value: int) -> int | None:
        if self.writable:
            return None
        offset = reduce(value, self.mask)
        if self.size:
            return self.base + mirror(offset, self.size - self.base)
        return self.base + offset

    def logical_address(self, value: int, near: int | None = None) -> int:
        for bank_lo, bank_hi in self._ranges_near(near):
            for window in self.windows:
                logical = self._logical_in(value, bank_lo, bank_hi, window)
                if logical is not None:
                    return logical
        raise ValueError(f"physical ${value:06X} is not reachable through this region")

    def _ranges_near(self, near: int | None) -> list[tuple[int, int]]:
        if near is None:
            return self.ranges
        bank = near >> 16
        current = [r for r in self.ranges if r[0] <= bank <= r[1]]
        return current + [r for r in self.ranges if r not in current]

    def _logical_in(self, value: int, bank_lo: int, bank_hi: int, window: tuple[int, int]) -> int | None:
        span = (self.size - self.base) if self.size else 1 << 24
        fill = ((bank_lo << 16) | window[0]) & self.mask
        offset = value - self.base
        while offset < 1 << 24:
            logical = _expand(offset, self.mask) | fill
            if self._reaches(logical, value, bank_lo, bank_hi, window):
                return logical
            if logical >> 16 > bank_hi:
                return None
            offset += span
        return None

    def _reaches(self, logical: int, value: int, bank_lo: int, bank_hi: int, window: tuple[int, int]) -> bool:
        """`logical` sits in this bank range and window and maps to physical `value`."""
        bank, addr = logical >> 16, logical & 0xFFFF
        in_window = bank_lo <= bank <= bank_hi and window[0] <= addr <= window[1]
        return in_window and self.physical_address(logical) == value


Region = BusRegion


class Bus:
    """Cartridge address space: regions resolved by (bank, address).

    Each bank keeps a short list of address windows; the most recently
    declared window covering an address wins. A legacy `.map` region
    (`map`) claims its banks whole, as it always has; a `BsnesRegion`
    (`map_region`) claims only its window, so ROM and SRAM can share banks.
    """

    def __init__(self, name: str | None = None) -> None:
        self.name = name
        self.windows: dict[int, list[tuple[int, int, str]]] = {}
        # Banks a single region claims whole (every legacy `.map` bank): one
        # dict hit on the hot path instead of a window scan.
        self._whole_bank: dict[int, Region] = {}
        self.mappings: dict[str, Region] = {}
        # identifier -> declaration shape (`BusMapping.shape()`), recorded by
        # `mappers.map_on_bus` so redeclarations compare what was written.
        self.declared: dict[str, tuple[object, ...]] = {}
        self.editable = True

    def has_mappings(self) -> bool:
        return self.mappings != {}

    def get_mapping_for_bank(self, bank: int) -> Region:
        return self.get_mapping(bank << 16 | self._last_window(bank)[0])

    def get_mapping(self, logical: int) -> Region:
        bank = logical >> 16
        whole = self._whole_bank.get(bank)
        if whole is not None:
            return whole
        addr = logical & 0xFFFF
        for lo, hi, identifier in reversed(self.windows.get(bank, [])):
            if lo <= addr <= hi:
                return self.mappings[identifier]
        from a816.exceptions import UnmappedBankError

        raise UnmappedBankError(bank, mapped_banks=list(self.windows))

    def _last_window(self, bank: int) -> tuple[int, int, str]:
        windows = self.windows.get(bank)
        if not windows:
            from a816.exceptions import UnmappedBankError

            raise UnmappedBankError(bank, mapped_banks=list(self.windows))
        return windows[-1]

    def _check_editable(self) -> None:
        if self.editable is not True:
            raise RuntimeError("Bus cannot be edited.")

    def map(
        self,
        identifier: str,
        bank_range: tuple[int, int],
        address_range: tuple[int, int],
        mask: int,
        writeable: bool = False,
        mirror_bank_range: tuple[int, int] | None = None,
    ) -> None:
        self._check_editable()
        self._claim_banks(identifier, Mapping(bank_range, address_range, mask, writeable), bank_range)
        if mirror_bank_range:
            mirror_mapping = Mapping(mirror_bank_range, address_range, mask, writeable)
            self._claim_banks(f"{identifier}_mirror", mirror_mapping, mirror_bank_range)

    def _claim_banks(self, identifier: str, mapping: Mapping, bank_range: tuple[int, int]) -> None:
        self.mappings[identifier] = mapping
        for bank in range(bank_range[0], bank_range[1] + 1):
            self.windows[bank] = [(0x0000, 0xFFFF, identifier)]
            self._whole_bank[bank] = mapping

    def map_region(self, identifier: str, region: BsnesRegion) -> None:
        self._check_editable()
        self.mappings[identifier] = region
        for bank_lo, bank_hi in region.ranges:
            for bank in range(bank_lo, bank_hi + 1):
                for lo, hi in region.windows:
                    self.windows.setdefault(bank, []).append((lo, hi, identifier))
                self._whole_bank.pop(bank, None)
                if region.windows == [(0x0000, 0xFFFF)]:
                    self._whole_bank[bank] = region

    def unmap(self, identifier: str) -> None:
        self._check_editable()
        gone = {identifier, f"{identifier}_mirror"}
        for name in gone:
            self.mappings.pop(name, None)
            self.declared.pop(name, None)
        kept = {bank: [w for w in windows if w[2] not in gone] for bank, windows in self.windows.items()}
        self.windows = {bank: windows for bank, windows in kept.items() if windows}
        self._whole_bank = {
            bank: self.mappings[windows[-1][2]]
            for bank, windows in self.windows.items()
            if windows[-1][:2] == (0x0000, 0xFFFF)
        }

    def get_address(self, addr: int) -> "Address":
        return Address(self, addr)

    def contiguous(self, last: int, first: int) -> bool:
        """Whether the bytes at logical `last` and `first` are consecutive in
        the ROM (a LoROM `$80:FFFF` -> `$81:8000` edge, a HiROM `$C0:FFFF` ->
        `$C1:0000` one): the edges a `cross_bank` alloc may run over."""
        last_physical = self.get_address(last).physical
        return last_physical is not None and self.get_address(first).physical == last_physical + 1

    def windows_in(self, bank: int, writable: bool) -> list[tuple[int, int]]:
        """Address windows of `bank` that regions of the given kind (ROM or
        writable memory) actually serve. A legacy `.map` claims its banks
        whole but only serves its `addr_range`: outside it, `physical_address`
        folds onto the same ROM bytes, so it must not count as more room."""
        out: list[tuple[int, int]] = []
        for lo, hi, identifier in self.windows.get(bank, []):
            region = self.mappings[identifier]
            if region.writable != writable:
                continue
            if isinstance(region, Mapping):
                lo, hi = max(lo, region.address_range[0]), min(hi, region.address_range[1])
            if lo <= hi:
                out.append((lo, hi))
        return sorted(out)


class Address:
    def __init__(self, bus: Bus, logical_value: int) -> None:
        self.bus: Bus = bus
        self.logical_value: int = logical_value
        self.mapping: Region = self._get_mapping()

    def _get_bank(self) -> int:
        return self.logical_value >> 16

    def _get_mapping(self) -> Region:
        from a816.exceptions import UnmappedBankError

        try:
            return self.bus.get_mapping(self.logical_value)
        except UnmappedBankError as exc:
            # Re-raise with the full logical address so the diagnostic can show
            # the offending `$xxyyzz`, not just the bank byte.
            exc.logical_address = self.logical_value
            raise

    @property
    def physical(self) -> int | None:
        return self.mapping.physical_address(self.logical_value)

    @property
    def writable(self) -> bool:
        return self.mapping.writable

    def __add__(self, other: Any) -> "Address":
        if isinstance(other, int):
            mapping = self._get_mapping()
            physical_address = mapping.physical_address(self.logical_value)
            if physical_address is not None:
                logical_address = mapping.logical_address(physical_address + other, near=self.logical_value)
            else:
                logical_address = self.logical_value + other
            return Address(self.bus, logical_address)
        else:
            raise ValueError("Address can only be added with ints.")  # noqa: TRY004


class LinearAddress(Address):
    """A placement-independent PC for object-mode `.alloc` body binding.

    An `Address` subtype (so it flows through every node's `pc_after`) that
    deliberately carries no bus mapping: `+ n` advances by exactly n bytes, so
    two labels in the same section always differ by their true byte distance.
    Object mode never needs a label's final logical address - the linker
    assigns it by rebasing the whole section (its `.map` is replayed at link) -
    only consistent intra-section offsets. Walking a real bus here would fold a
    mirror-region bank stride into the PC and land a forward label a bank-size
    too high. `physical` mirrors `logical_value` so byte-count measurement
    (`pc.physical - start.physical`) stays exact.
    """

    def __init__(self, logical_value: int) -> None:
        # No bus/mapping: object-mode binding is pure offset arithmetic.
        self.bus = None  # type: ignore[assignment]
        self.logical_value = logical_value
        self.mapping = None  # type: ignore[assignment]

    @property
    def physical(self) -> int:
        return self.logical_value

    def __add__(self, other: Any) -> "LinearAddress":
        if isinstance(other, int):
            return LinearAddress(self.logical_value + other)
        raise ValueError("LinearAddress can only be added with ints.")

    def __repr__(self) -> str:
        return f"LinearAddress({self.logical_value:#06x})"
