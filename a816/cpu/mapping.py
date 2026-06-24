from typing import Any


class Mapping:
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

    def logical_address(self, value: int) -> int:
        bank = value // self.mask

        return (bank + self.bank_range[0]) << 16 | (self.mask & 0xFFFF) + value % self.mask


class Bus:
    def __init__(self, name: str | None = None) -> None:
        self.name = name
        self.lookup: dict[int, str] = {}
        self.inverse_lookup: dict[str, int] = {}
        self.mappings: dict[str, Mapping] = {}
        self.editable = True
        self.internal_id = 0

    def has_mappings(self) -> bool:
        return self.mappings != {}

    def get_mapping_for_bank(self, bank: int) -> Mapping:
        try:
            return self.mappings[self.lookup[bank]]
        except KeyError as exc:
            from a816.exceptions import UnmappedBankError

            raise UnmappedBankError(bank, mapped_banks=list(self.lookup.keys())) from exc

    def map(
        self,
        identifier: str,
        bank_range: tuple[int, int],
        address_range: tuple[int, int],
        mask: int,
        writeable: bool = False,
        mirror_bank_range: tuple[int, int] | None = None,
    ) -> None:
        if self.editable is not True:
            raise RuntimeError("Bus cannot be edited.")

        self.mappings[identifier] = Mapping(bank_range, address_range, mask, writeable)

        for bank in range(bank_range[0], bank_range[1] + 1):
            self.lookup[bank] = identifier

        if mirror_bank_range:
            mirror_identifier = f"{identifier}_mirror"
            self.mappings[mirror_identifier] = Mapping(mirror_bank_range, address_range, mask, writeable)

            for bank in range(mirror_bank_range[0], mirror_bank_range[1] + 1):
                self.lookup[bank] = mirror_identifier

    def unmap(self, identifier: str) -> None:
        if self.editable is not True:
            raise RuntimeError("Bus cannot be edited.")

        if identifier in self.mappings.keys():
            del self.mappings[identifier]
        mirror_identifier = f"{identifier}_mirror"
        if mirror_identifier in self.mappings.keys():
            del self.mappings[mirror_identifier]

    def get_address(self, addr: int) -> "Address":
        return Address(self, addr)


class Address:
    def __init__(self, bus: Bus, logical_value: int) -> None:
        self.bus: Bus = bus
        self.logical_value: int = logical_value
        self.mapping: Mapping = self._get_mapping()

    def _get_bank(self) -> int:
        return self.logical_value >> 16

    def _get_mapping(self) -> Mapping:
        from a816.exceptions import UnmappedBankError

        try:
            return self.bus.get_mapping_for_bank(self._get_bank())
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
                logical_address = mapping.logical_address(physical_address + other)
            else:
                logical_address = self.logical_value + other
            return Address(self.bus, logical_address)
        else:
            raise ValueError("Address can only be added with ints.")


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
