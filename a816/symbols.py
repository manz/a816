from a816.cpu.cpu_65c816 import rom_to_snes, RomType
from a816.exceptions import SymbolNotDefined


class Scope(object):
    def __init__(self, resolver, parent=None):
        self.symbols = {}
        self.parent = parent
        self.resolver = resolver
        self.table = None

    def add_label(self, label, value):
        self.add_symbol(label, rom_to_snes(value, self.resolver.rom_type))

    def add_symbol(self, symbol, value):
        if symbol in self.symbols:
            raise RuntimeError('Symbol already defined (%s)' % symbol)
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
        if symbol == 'org':
            return self.resolver.snes_pc

        if self.parent:
            if symbol in self.symbols:
                return self[symbol]
            else:
                return self.parent.value_for(symbol)
        else:
            return self[symbol]


class NamedScope(Scope):
    def __init__(self, name, resolver, parent=None):
        super().__init__(resolver, parent)
        self.name = name


class Resolver(object):
    def __init__(self, pc=0x000000):
        self.symbol_map = {}
        self.pc = pc
        self.a = False
        self.x = False
        self.rom_type = RomType.low_rom
        self.current_scope_index = 0
        self.last_used_scope = 0
        self.current_scope = Scope(self)
        self.scopes = [self.current_scope]

    def append_named_scope(self, name):
        scope = NamedScope(name, self, self.current_scope)
        self.scopes.append(scope)

    def append_scope(self):
        scope = Scope(self, self.current_scope)
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
            print("Scope\n")
            self._dump_symbols(scope.symbols)
