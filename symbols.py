from cpu_65c816 import rom_to_snes, RomType


class Resolver(object):
    def __init__(self, pc=0x0000000):
        self.symbol_map = {}
        self.pc = pc
        self.a = False
        self.x = False
        self.rom_type = RomType.low_rom

    @property
    def snes_pc(self):
        return rom_to_snes(self.pc, self.rom_type)

    def add_symbol(self, symbol, value):
        self.symbol_map[symbol] = value

    def value_for(self, symbol):
        return rom_to_snes(self.symbol_map.get(symbol, None), self.rom_type)
