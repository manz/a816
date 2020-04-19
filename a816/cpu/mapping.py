class Mapping(object):
    def __init__(self, bank_range, address_range, mask, writeable=False, mirror=None):
        self.bank_range = bank_range
        self.mirror = mirror
        self.address_range = address_range
        self.mask = mask
        self.writable = writeable

    def _resolve_address(self, value):
        bank = value >> 16
        if self.writable is False:
            return (bank - self.bank_range[0]) * self.mask + (value & ~self.mask & 0xffff)
        else:
            return None

    def physical_address(self, value):
        bank = value >> 16
        if self.writable is False:
            return (bank - self.bank_range[0]) * self.mask + (value & ~self.mask & 0xffff)
        else:
            return None

    def logical_address(self, value):
        bank = value // self.mask

        return (bank + self.bank_range[0]) << 16 | (self.mask & 0xffff) + value % self.mask


class Bus(object):

    def __init__(self, name=None):
        self.name = name
        self.lookup = {}
        self.inverse_lookup = {}
        self.mappings = {}
        self.editable = True
        self.internal_id = 0

    def has_mappings(self) -> bool:
        return self.mappings != {}

    def get_mapping_for_bank(self, bank) -> Mapping:
        return self.mappings[self.lookup[bank]]

    def map(self, identifier, bank_range, address_range, mask, writeable=False, mirror_bank_range=None):
        if self.editable is not True:
            raise Exception('Bus cannot be edited.')
        # identifier = identifier or ++self.internal_id

        self.mappings[identifier] = Mapping(bank_range,
                                            address_range,
                                            mask,
                                            writeable)

        for bank in range(bank_range[0], bank_range[1] + 1):
            self.lookup[bank] = identifier

        if mirror_bank_range:
            mirror_identifier = f'{identifier}_mirror'
            self.mappings[mirror_identifier] = Mapping(mirror_bank_range,
                                                       address_range,
                                                       mask,
                                                       writeable)

            for bank in range(mirror_bank_range[0], mirror_bank_range[1] + 1):
                self.lookup[bank] = mirror_identifier

    def unmap(self, identifier):
        if self.editable is not True:
            raise Exception('Bus cannot be edited.')

        if identifier in self.mappings.keys():
            del self.mappings[identifier]
        mirror_identifier = f'{identifier}_mirror'
        if mirror_identifier in self.mappings.keys():
            del self.mappings[mirror_identifier]

    def get_address(self, addr: int) -> 'Address':
        return Address(self, addr)


class Address(object):
    def __init__(self, bus: Bus, logical_value: int):
        self.bus: Bus = bus
        self.logical_value: int = logical_value
        self.mapping: Mapping = self._get_mapping()

    def _get_bank(self) -> int:
        return self.logical_value >> 16

    def _get_mapping(self) -> Mapping:
        return self.bus.get_mapping_for_bank(self._get_bank())

    @property
    def physical(self) -> int:
        return self.mapping.physical_address(self.logical_value)

    @property
    def writable(self) -> bool:
        return self.mapping.writable

    def __add__(self, other: int) -> 'Address':
        if type(other) == int:
            mapping = self._get_mapping()
            physical_address = mapping.physical_address(self.logical_value)
            if physical_address is not None:
                logical_address = mapping.logical_address(physical_address + other)
            else:
                logical_address = self.logical_value + other
            return Address(self.bus, logical_address)
        else:
            raise ValueError('Address can only be added with ints.')
