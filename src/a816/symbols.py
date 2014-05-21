from .cpu.cpu_65c816 import rom_to_snes, RomType


class Scope(object):
    def __init__(self, resolver, parent=None):
        self.symbols = {}
        self.parent = parent
        self.resolver = resolver

    def add_label(self, label, value):
        if label in self.symbols:
            raise RuntimeError('Label already defined')
        self.symbols[label] = rom_to_snes(value, self.resolver.rom_type)

    def add_symbol(self, symbol, value):
        if symbol in self.symbols:
            raise RuntimeError('Symbol already defined')
        self.symbols[symbol] = value

    def value_for(self, symbol):
        if symbol == 'org':
            return self.resolver.snes_pc

        if self.parent:
            if symbol in self.symbols:
                return self.symbols[symbol]
            else:
                return self.parent.value_for(symbol)
        else:
            return self.symbols[symbol]


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

    def append_scope(self):
        scope = Scope(self, self.current_scope)
        self.scopes.append(scope)

    def use_next_scope(self):
        self.last_used_scope += 1
        self.current_scope = self.scopes[self.last_used_scope]

    def restore_scope(self):
        self.current_scope = self.current_scope.parent

    @property
    def snes_pc(self):
        return rom_to_snes(self.pc, self.rom_type)

    def dump_symbol_map(self):
        for scope in self.scopes:
            print("Scope\n")
            for (key, value) in scope.symbols.items():
                print('%s 0x%02x' % (key.ljust(32), value))
