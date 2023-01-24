from typing import Dict, Optional, Tuple


class Mapping:
    def __init__(
        self,
        bank_range: Tuple[int, int],
        address_range: Tuple[int, int],
        mask: int,
        writeable: bool = False,
        mirror: Optional[Tuple[int, int]] = None,
    ) -> None:
        self.bank_range = bank_range
        self.mirror = mirror
        self.address_range = address_range
        self.mask = mask
        self.writable = writeable

    def physical_address(self, value: int) -> Optional[int]:
        bank = value >> 16
        if self.writable is False:
            return (bank - self.bank_range[0]) * self.mask + (value & ~self.mask & 0xFFFF)
        else:
            return None

    def logical_address(self, value: int) -> int:
        bank = value // self.mask

        return (bank + self.bank_range[0]) << 16 | (self.mask & 0xFFFF) + value % self.mask


class Bus:
    def __init__(self, name: Optional[str] = None) -> None:
        self.name = name
        self.lookup: Dict[int, str] = {}
        self.inverse_lookup: Dict[str, int] = {}
        self.mappings: Dict[str, Mapping] = {}
        self.editable = True
        self.internal_id = 0

    def has_mappings(self) -> bool:
        return self.mappings != {}

    def get_mapping_for_bank(self, bank: int) -> Mapping:
        return self.mappings[self.lookup[bank]]

    def map(
        self,
        identifier: str,
        bank_range: Tuple[int, int],
        address_range: Tuple[int, int],
        mask: int,
        writeable: bool = False,
        mirror_bank_range: Optional[Tuple[int, int]] = None,
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
        return self.bus.get_mapping_for_bank(self._get_bank())

    @property
    def physical(self) -> Optional[int]:
        return self.mapping.physical_address(self.logical_value)

    @property
    def writable(self) -> bool:
        return self.mapping.writable

    def __add__(self, other: int) -> "Address":
        if type(other) == int:
            mapping = self._get_mapping()
            physical_address = mapping.physical_address(self.logical_value)
            if physical_address is not None:
                logical_address = mapping.logical_address(physical_address + other)
            else:
                logical_address = self.logical_value + other
            return Address(self.bus, logical_address)
        else:
            raise ValueError("Address can only be added with ints.")
