import logging

from a816.cpu.cpu_65c816 import rom_to_snes, RomType
from a816.exceptions import SymbolNotDefined
from a816.cpu.cpu_65c816 import snes_to_rom

logger = logging.getLogger('a816')


class Scope(object):
    def __init__(self, resolver, parent=None):
        self.symbols = {}
        self.parent = parent
        self.resolver = resolver
        self.table = None

    def add_label(self, label, value):
        if self.resolver.reloc:
            self.add_symbol(label, value)
        else:
            self.add_symbol(label, rom_to_snes(value, self.resolver.rom_type))

    def add_symbol(self, symbol, value):
        if symbol in self.symbols:
            logger.warning('Symbol already defined (%s)' % symbol)
        self.symbols[symbol] = value

    def __getitem__(self, item):
        try:
            return self.symbols[item]
        except KeyError as e:
            raise SymbolNotDefined('%s is not defined.' % item) from e

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


class Resolver(object):
    def __init__(self, pc=0x000000):
        self.symbol_map = {}
        self.pc = pc
        self.reloc_address = pc
        self.reloc = False
        self.a = False
        self.x = False
        self.rom_type = RomType.low_rom
        self.current_scope_index = 0
        self.last_used_scope = 0
        self.current_scope = Scope(self)
        self.scopes = [self.current_scope]

    def set_position(self, pc, reloc_address=None):
        if reloc_address is not None:
            self.pc = pc
            self.reloc_address = reloc_address
            self.reloc = True
        else:
            self.pc = snes_to_rom(pc)
            self.reloc_address = pc
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

    @property
    def snes_pc(self):
        return rom_to_snes(self.pc, self.rom_type)

    def _dump_symbols(self, symbols):
        keys = sorted(symbols.keys())
        for key in keys:
            value = symbols[key]
            if isinstance(value, dict):
                print('namedscope', key)
                self._dump_symbols(value)
            else:
                print('%s 0x%02x' % (key.ljust(32), value))

    def dump_symbol_map(self):
        for scope in self.scopes:
            if not isinstance(scope, InternalScope):
                print("Scope\n")
                self._dump_symbols(scope.symbols)
