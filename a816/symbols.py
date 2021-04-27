import logging

from a816.cpu.cpu_65c816 import RomType
from a816.cpu.mapping import Bus
from a816.exceptions import SymbolNotDefined

logger = logging.getLogger('a816')


class Scope(object):
    def __init__(self, resolver, parent=None):
        self.symbols = {}
        self.parent = parent
        self.resolver: Resolver = resolver
        self.table = None
        self.labels = {}

    def add_label(self, label, value):
        self.labels[label] = value.logical_value
        self.add_symbol(label, value.logical_value)

    def get_labels(self):
        return self.labels.items()

    def add_symbol(self, symbol, value):
        if symbol in self.symbols:
            logger.warning('Symbol already defined (%s)' % symbol)
        self.symbols[symbol] = value

    def __getitem__(self, item):
        try:
            return self.symbols[item]
        except KeyError as e:
            raise SymbolNotDefined(item) from e

    def get_table(self):
        if self.table is None:
            if self.parent:
                return self.parent.get_table()
        else:
            return self.table

    def value_for(self, symbol):
        if self.parent:
            if symbol in self.symbols:
                return self[symbol]
            else:
                return self.parent.value_for(symbol)
        else:
            return self[symbol]


class InternalScope(Scope):
    pass


class NamedScope(Scope):
    def __init__(self, name, resolver, parent=None):
        super().__init__(resolver, parent)
        self.name = name


low_rom_bus = Bus('low_rom_default_mapping')

low_rom_bus.map(1, (0x00, 0x6f), (0x8000, 0xffff), mask=0x8000, mirror_bank_range=(0x80, 0xcf))
low_rom_bus.map(2, (0x7e, 0x7f), (0, 0xffff), mask=0x1_0000)

low_rom_bus.editable = False

high_rom_bus = Bus('high_rom_default_mapping')

high_rom_bus.map(1, (0x40, 0x7f), (0, 0xffff), mask=0x1_0000, mirror_bank_range=(0xc0, 0xff))
high_rom_bus.map(2, (0x7e, 0x7f), (0, 0xffff), mask=0x1_0000, writeable=1)

high_rom_bus.editable = False

BUS_MAPPING = {
    RomType.low_rom: low_rom_bus,
    RomType.high_rom: high_rom_bus
}


class Resolver(object):
    def __init__(self, pc=0x000000):
        self.symbol_map = {}
        self.reloc = False
        self.a = False
        self.x = False
        self.rom_type = RomType.low_rom
        self.current_scope_index = 0
        self.last_used_scope = 0
        self.current_scope: Scope = Scope(self)
        self.scopes = [self.current_scope]
        self.bus = Bus()
        self.pc = 0
        self.reloc_address = 0
        self.set_position(pc)

    def get_bus(self) -> Bus:
        if self.bus.has_mappings():
            bus = self.bus
        else:
            bus = BUS_MAPPING[self.rom_type]
        return bus

    def set_position(self, pc):
        addr = self.get_bus().get_address(pc)
        physical = addr.physical

        if physical is not None:
            self.pc = addr.physical

        self.reloc_address = addr
        self.reloc = False

    def append_named_scope(self, name):
        scope = NamedScope(name, self, self.current_scope)
        self.scopes.append(scope)

    def append_scope(self):
        scope = Scope(self, self.current_scope)
        self.scopes.append(scope)

    def append_internal_scope(self):
        scope = InternalScope(self, self.current_scope)
        self.scopes.append(scope)

    def use_next_scope(self):
        self.last_used_scope += 1
        self.current_scope = self.scopes[self.last_used_scope]

    def restore_scope(self, exports=False):
        if exports and isinstance(self.current_scope, NamedScope):
            scope = self.current_scope
            parent_symbols = scope.parent.symbols
            parent_symbols[scope.name] = scope.symbols
        self.current_scope = self.current_scope.parent

    def _dump_symbols(self, symbols):
        keys = sorted(symbols.keys())
        for key in keys:
            value = symbols[key]
            if isinstance(value, dict):
                print('namedscope', key)
                self._dump_symbols(value)
            else:
                if isinstance(value, tuple):
                    print('%s %s' % (key.ljust(32), value))
                else:
                    print('%s 0x%02x' % (key.ljust(32), value))

    def dump_symbol_map(self):
        for scope in self.scopes:
            if not isinstance(scope, InternalScope):
                print("Scope\n")
                self._dump_symbols(scope.symbols)

    def get_all_labels(self):
        labels = []
        for scope in self.scopes:
            if not isinstance(scope, InternalScope):
                labels += scope.get_labels()

        return labels
